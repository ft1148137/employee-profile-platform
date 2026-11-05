from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlmodel import Session, col, select

from app.authz.deps import OrgAdminRequired
from app.authz.principal import Principal
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.capability.models import CapabilityDefinition
from app.domains.capability.service import SchemaNotFound, get_schema, list_capabilities
from app.domains.change_request.models import (
    ChangeItem,
    ItemDecision,
    ProfileChangeRequest,
    RequestStatus,
    ReviewDecision,
    ReviewVia,
)
from app.domains.change_request.service import transition
from app.domains.org.models import Organization
from app.domains.review.models import ApprovalPolicy
from app.models import User


class PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    require_manager: StrictBool = True
    category_overrides: dict[str, StrictBool] = Field(
        default_factory=dict, max_length=100
    )
    owner_review_days: int = Field(default=14, ge=1, le=90, strict=True)


class PolicyPublic(PolicyInput):
    version: int = 0
    created_at: datetime | None = None
    created_by_user_id: UUID | None = None
    created_by_name: str | None = None
    changed_fields: list[str] = Field(default_factory=list)


class PolicyView(BaseModel):
    current: PolicyPublic
    categories: list[str]
    history: list[PolicyPublic]


class InvalidPolicy(HTTPException):
    def __init__(self) -> None:
        super().__init__(422, "审批规则包含当前词典中不存在的能力类别")


def read_policy(session: Session, org_id: UUID) -> PolicyPublic:
    row = session.exec(
        select(ApprovalPolicy)
        .where(ApprovalPolicy.org_id == org_id)
        .order_by(col(ApprovalPolicy.version).desc())
    ).first()
    return (
        PolicyPublic.model_validate(row, from_attributes=True)
        if row
        else PolicyPublic()
    )


def requires_manager(policy: PolicyInput, category: str | None) -> bool:
    return (
        policy.category_overrides.get(category, policy.require_manager)
        if category
        else policy.require_manager
    )


def policy_view(session: Session, org_id: UUID) -> PolicyView:
    try:
        schema = get_schema(session, org_id)
        categories = sorted(
            {
                c.category
                for c in list_capabilities(session, org_id, schema.id)
                if c.category
            }
        )
    except SchemaNotFound:
        categories = []
    history = []
    previous = PolicyInput().model_dump()
    for row, user in session.exec(
        select(ApprovalPolicy, User)
        .join(User, col(User.id) == ApprovalPolicy.created_by_user_id)
        .where(ApprovalPolicy.org_id == org_id)
        .order_by(col(ApprovalPolicy.version))
    ).all():
        current = PolicyInput.model_validate(row, from_attributes=True).model_dump()
        history.append(
            PolicyPublic.model_validate(row, from_attributes=True).model_copy(
                update={
                    "created_by_name": user.full_name or user.email or user.username,
                    "changed_fields": [
                        key for key in current if current[key] != previous[key]
                    ],
                }
            )
        )
        previous = current
    return PolicyView(
        current=history[-1] if history else PolicyPublic(),
        categories=categories,
        history=list(reversed(history)),
    )


def save_policy(
    session: Session, principal: Principal, body: PolicyInput
) -> PolicyView:
    if not principal.is_org_admin or principal.user_id is None:
        raise OrgAdminRequired()
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    old = policy_view(session, principal.org_id)
    if body.category_overrides.keys() - (
        set(old.categories) | old.current.category_overrides.keys()
    ):
        raise InvalidPolicy()
    row = ApprovalPolicy(
        org_id=principal.org_id,
        version=old.current.version + 1,
        created_by_user_id=principal.user_id,
        **body.model_dump(),
    )
    session.add(row)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="approval_policy.saved",
        target_type="approval_policy",
        target_id=str(row.id),
        details={
            "version": row.version,
            "before": old.current.model_dump(mode="json"),
            "after": body.model_dump(mode="json"),
        },
    )
    session.commit()
    return policy_view(session, principal.org_id)


def apply_policy(
    session: Session,
    principal: Principal,
    request: ProfileChangeRequest,
    items: list[ChangeItem],
) -> None:
    from app.domains.review.publish import publish

    policy = read_policy(session, principal.org_id)
    categories = dict(
        session.exec(
            select(CapabilityDefinition.id, CapabilityDefinition.category).where(
                CapabilityDefinition.org_id == principal.org_id,
                col(CapabilityDefinition.id).in_(
                    {i.capability_definition_id for i in items}
                ),
            )
        ).all()
    )
    approved = [
        i
        for i in items
        if not requires_manager(policy, categories[i.capability_definition_id])
    ]
    if not approved:
        return
    decision = ReviewDecision(
        org_id=request.org_id,
        request_id=request.id,
        via=ReviewVia.POLICY,
        request_revision=request.revision,
        comment=f"审批规则第 {policy.version} 版",
    )
    session.add(decision)
    session.flush()
    for item in approved:
        item.decision = ItemDecision.APPROVED
        item.review_decision_id = decision.id
    version = publish(session, request, approved, decision)
    if len(approved) == len(items):
        transition(request, RequestStatus.APPROVED)
        request.decided_at = utc_now()
    record_audit(
        session,
        org_id=request.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="change_request.policy_published",
        target_type="profile_change_request",
        target_id=str(request.id),
        details={
            "policy_version": policy.version,
            "decision_id": str(decision.id),
            "version_id": str(version.id),
        },
    )
