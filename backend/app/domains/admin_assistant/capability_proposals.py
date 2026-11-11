"""Proposal decisions share the manual service and change-set undo protocol."""

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue
from sqlmodel import Session

from app.authz.principal import Principal
from app.domains.capability import proposals
from app.domains.capability.schemas import CapabilityProposalSummary

if TYPE_CHECKING:
    from app.domains.admin_assistant.registry import AdminFunction


class ProposalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID


class ProposalSnapshot(BaseModel):
    name: str
    id: UUID
    status: str
    updated_at: str


class ProposalSummaries(BaseModel):
    data: list[CapabilityProposalSummary]


def snapshot(db: Session, p: Principal, args: ProposalArgs) -> ProposalSnapshot:
    row = proposals.get_proposal(db, p, args.id)
    return ProposalSnapshot(
        name=row.name,
        id=row.id,
        status=row.status,
        updated_at=row.updated_at.isoformat(),
    )


def reverse(
    db: Session, p: Principal, before: dict[str, JsonValue]
) -> tuple[str, BaseModel]:
    args = ProposalArgs.model_validate({"id": before["id"]})
    proposals.require_restorable(db, p, proposals.get_proposal(db, p, args.id))
    return "dismiss_capability_proposal", args


def dismiss(
    db: Session, p: Principal, args: ProposalArgs, *, commit: bool = True
) -> BaseModel:
    # Only the confirmed reverse change set can restore a dismissed suggestion.
    undo_of = db.info.get("audit_context", {}).get("undo_of_id")
    if undo_of:
        from app.domains.admin_assistant.change_set import get, public

        original = public(get(db, p, UUID(undo_of)))
        if not any(
            item.function == "dismiss_capability_proposal"
            and item.args.get("id") == str(args.id)
            for item in original.items
        ):
            raise proposals.ProposalConflict("撤销对象与原改动不一致，请重新撤销")
    proposals.dismiss_proposal(db, p, args.id, restore=bool(undo_of), commit=commit)
    return snapshot(db, p, args)


def register_proposals(functions: dict[str, AdminFunction]) -> None:
    from app.domains.admin_assistant.registry import AdminFunction, NoArgs

    functions["list_capability_proposals"] = AdminFunction(
        "list_capability_proposals",
        "rules",
        "read",
        "normal",
        "查看起草时提议的新能力",
        NoArgs,
        lambda d, p, a: ProposalSummaries(
            data=[
                CapabilityProposalSummary.model_validate(row.model_dump())
                for row in proposals.list_proposals(d, p).data
            ]
        ),
        "/admin/rules?tab=dictionary",
        (("GET", "/capabilities/proposals"),),
    )
    functions["dismiss_capability_proposal"] = AdminFunction(
        "dismiss_capability_proposal",
        "rules",
        "change",
        "normal",
        "忽略起草时提议的新能力（可撤销）",
        ProposalArgs,
        dismiss,
        "/admin/rules?tab=dictionary",
        (("POST", "/capabilities/proposals/{id}/dismiss"),),
        snapshot=snapshot,
        reverse=reverse,
    )
