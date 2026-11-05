"""M4 gate: existing channel boundaries plus a Qiguang approval walkthrough."""

# ruff: noqa: F401

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.authz.deps import get_org_principal
from app.authz.principal import Principal
from app.core.security import create_access_token
from app.domains.audit.models import AuditEvent
from app.domains.change_request import employee
from app.domains.change_request.models import ItemDecision, ProfileChangeRequest
from app.domains.change_request.schemas import ItemInput
from app.domains.drafting import service as drafting_service
from app.domains.evidence.models import Evidence
from app.domains.model.schemas import ModelSettingUpdate
from app.domains.org.models import OrgMembership, RoleGrant
from app.domains.profile.models import ProfileVersion
from app.domains.review import appeal
from app.domains.review import service as review_service
from app.domains.review.appeal_schemas import ResolveAppealInput
from app.domains.review.publish import current_claims
from app.domains.review.routing import reviewers_for
from app.models import User
from tests.acceptance.test_m2 import evaluation

# Collect the original assertions and parametrizations, without copying scenarios.
from tests.api.routes.test_appeals import (
    test_appeal_lifecycle as test_appeal_lifecycle,
)
from tests.api.routes.test_approval_policy import (
    test_owner_submission_publishes_tentative_without_manager as test_policy,
)
from tests.api.routes.test_manager_mcp import (
    test_review_visibility_and_no_self_approval as test_mcp_self_approval,
)
from tests.api.routes.test_review_delegations import (
    test_cross_department_employee_delegate_evidence_is_request_scoped as test_delegation,
)
from tests.api.routes.test_reviews import (
    test_admin_without_employee_is_fallback_and_access_expires as test_manager_request,
)
from tests.api.routes.test_reviews import (
    test_mixed_and_all_return_or_reject as test_mixed_decisions,
)
from tests.api.routes.test_reviews import (
    test_self_review_always_forbidden as test_web_self_approval,
)
from tests.api.routes.test_reviews import values
from tests.authz.test_tenant_access import tenant_data as tenant_data
from tests.domains.test_bot_reviews import (
    test_review_confirmation_guards as test_im_confirmation_guards,
)
from tests.domains.test_bot_reviews import (
    test_telegram_review_roundtrip as test_telegram_review,
)
from tests.domains.test_drafting_model import drafting as drafting
from tests.domains.test_feishu import (
    service,
)
from tests.domains.test_feishu import (
    test_review_card_confirmation as test_feishu_review,
)
from tests.domains.test_feishu import (
    test_turn_submit_withdraw as test_feishu_submission,
)
from tests.domains.test_im_conversation import (
    test_telegram_turn_submit_withdraw as test_telegram_submission,
)
from tests.domains.test_im_wecom import (
    test_conversation as test_wecom_submission,
)
from tests.domains.test_im_wecom import (
    test_review_card_over_wecom as test_wecom_review,
)
from tests.domains.test_incremental_drafting import (
    incremental_context as incremental_context,
)
from tests.domains.test_incremental_drafting import (
    test_attach_publishes_without_pending_or_notification as test_incremental_evidence,
)
from tests.domains.test_incremental_drafting import (
    test_modify_requires_review_and_replacement_retains_records as test_incremental_judgment,
)
from tests.domains.test_update_reminders import (
    test_due_once_and_downtime as test_reminders,
)
from tests.domains.test_update_reminders import (
    test_stale_flag_changes_immediately as test_stale_evidence,
)
from tests.utils.pi import PiHarness

pytestmark = pytest.mark.xdist_group("model")


def test_qiguang_jira_to_second_version(
    db: Session, client: TestClient, pi_service: PiHarness
) -> None:
    pi_service.auto_draft = True
    users: list[UUID] = []
    try:
        with evaluation["enterprise"](
            "qiguang-aiot",
            ModelSettingUpdate(
                enabled=True,
                kind="openai_compatible",
                provider="openai-compatible",
                model="synthetic",
                base_url=pi_service.config.baseUrl,
            ),
        ) as (session, org, people):
            evaluation["ingest_dataset"](session, org, people, "qiguang-aiot")
            subject, manager = people["QG019"], people["QG002"]
            principals = []
            for person, role in ((subject, None), (manager, "dept_manager")):
                user = User(
                    username=f"m4-{uuid4()}@example.com", hashed_password="unused"
                )
                session.add(user)
                session.flush()
                users.append(user.id)
                member = OrgMembership(
                    org_id=org.id, user_id=user.id, employee_id=person.id
                )
                session.add(member)
                session.flush()
                if role:
                    session.add(
                        RoleGrant(
                            org_id=org.id,
                            membership_id=member.id,
                            role=role,
                            department_id=subject.department_id,
                        )
                    )
                session.commit()
                principals.append(get_org_principal(org.id, user, session))
            owner, reviewer = principals
            assert reviewers_for(session, org.id, subject.id) == [reviewer.user_id]
            evidence = {
                e.id: e.model_dump()
                for e in session.exec(select(Evidence).where(Evidence.org_id == org.id))
            }
            assert any(e["source"] == "jira" for e in evidence.values())
            drafting_service.run_drafting(
                Principal.system_worker(org.id), [str(subject.id)]
            )
            session.expire_all()
            initial = session.exec(
                select(ProfileChangeRequest).where(
                    ProfileChangeRequest.org_id == org.id,
                    ProfileChangeRequest.subject_employee_id == subject.id,
                )
            ).one()
            assert initial.status == "pending_owner"
            preview = employee.preview(owner, session, initial.id)
            sent = employee.submit(owner, session, initial.id, preview.revision, "web")
            session.commit()
            first = review_service.decide(
                reviewer, session, sent.id, sent.revision, values(sent)
            )
            session.commit()
            frozen = session.get(
                ProfileVersion, first.published_version_id
            ).model_dump()
            assert frozen["revision"] == 1
            claims = current_claims(session, org.id, subject.id)
            key = next(iter(claims))
            extra = [
                c.key
                for c in employee.capability_dictionary(owner, session).data
                if c.key != key
            ][:2]
            draft = employee.save_draft(
                owner,
                session,
                None,
                [
                    ItemInput(
                        op="modify",
                        capability_key=key,
                        statement="独立完成合成项目并补充本人确认的依据",
                        owner_statement="本人确认的合成更新依据",
                    )
                ]
                + [
                    ItemInput(
                        op="add",
                        capability_key=k,
                        statement="合成待核对能力",
                        owner_statement="合成说明",
                    )
                    for k in extra
                ],
                "web",
            )
            sent = employee.submit(owner, session, draft.id, draft.revision, "web")
            session.commit()
            decisions = values(sent)
            decisions[1].decision = ItemDecision.CHANGES_REQUESTED
            decisions[2].decision = ItemDecision.REJECTED
            second = review_service.decide(
                reviewer, session, sent.id, sent.revision, decisions
            )
            session.commit()
            returned = session.get(ProfileChangeRequest, second.returned_request_id)
            assert returned.status == "draft" and returned.parent_request_id == sent.id
            assert set(current_claims(session, org.id, subject.id)) == {key}
            version = session.get(ProfileVersion, second.published_version_id)
            assert (
                version.revision == 2
                and version.supersedes_version_id == first.published_version_id
            )
            assert (
                session.get(ProfileVersion, first.published_version_id).model_dump()
                == frozen
            )
            assert (
                current_claims(session, org.id, subject.id)[key].assessment_status
                == "confirmed"
            )
            auth = {
                "Authorization": f"Bearer {create_access_token(owner.user_id, expires_delta=timedelta(minutes=5))}"
            }
            base = f"/api/v1/orgs/{org.id}/employees/{subject.id}/change-requests"
            for request_id in (initial.id, sent.id):
                response = client.get(f"{base}/{request_id}/timeline", headers=auth)
                assert response.status_code == 200, response.text
                assert {e["action"] for e in response.json()} == {
                    "created",
                    "submitted",
                    "decided",
                    "published",
                } | ({"returned"} if request_id == sent.id else set())
            actions = {
                a.action
                for a in session.exec(
                    select(AuditEvent).where(AuditEvent.org_id == org.id)
                )
            }
            assert {
                "initial_profiles.drafted",
                "change_request.submitted",
                "change_request.decided",
            } <= actions
            assert pi_service.requests

            # Return/rejection preserve r2; appeal adds a dispute without editing it.
            for decision in (ItemDecision.CHANGES_REQUESTED, ItemDecision.REJECTED):
                draft = employee.save_draft(
                    owner,
                    session,
                    None,
                    [
                        ItemInput(
                            op="modify",
                            capability_key=key,
                            statement="待核对的合成更新",
                            owner_statement="合成说明",
                        )
                    ],
                    "web",
                )
                sent = employee.submit(owner, session, draft.id, draft.revision, "web")
                session.commit()
                result = review_service.decide(
                    reviewer, session, sent.id, sent.revision, values(sent, decision)
                )
                session.commit()
                assert result.published_version_id is None
                if decision == ItemDecision.CHANGES_REQUESTED:
                    returned = next(
                        r
                        for r in employee.my_requests(owner, session, "all").data
                        if r.id == result.returned_request_id
                    )
                    assert (
                        returned.status == "draft"
                        and returned.parent_request_id == sent.id
                    )
                else:
                    row = appeal.appeal_decision(
                        owner, session, sent.id, sent.items[0].id, "补充合成申诉说明"
                    )
                    session.commit()
                    assert (
                        next(
                            i
                            for i in employee.my_profile(owner, session).items
                            if i.capability.key == key
                        ).status
                        == "disputed"
                    )
                    admin_user = session.exec(
                        select(User).where(User.username == f"{org.slug}@example.com")
                    ).one()
                    admin_member = session.exec(
                        select(OrgMembership).where(
                            OrgMembership.org_id == org.id,
                            OrgMembership.user_id == admin_user.id,
                        )
                    ).one()
                    session.add(
                        RoleGrant(
                            org_id=org.id,
                            membership_id=admin_member.id,
                            role="org_admin",
                        )
                    )
                    session.commit()
                    admin = get_org_principal(org.id, admin_user, session)
                    resolution = appeal.resolve_appeal(
                        admin,
                        session,
                        row.id,
                        ResolveAppealInput(
                            status="reopened", reason="允许补充后重新提交"
                        ),
                    )
                    session.commit()
                    assert resolution.draft_request_id
                    own_draft = employee.save_draft(
                        reviewer,
                        session,
                        None,
                        [
                            ItemInput(
                                op="add",
                                capability_key=key,
                                statement="经理本人的合成能力",
                                owner_statement="经理本人确认的合成依据",
                            )
                        ],
                        "web",
                    )
                    own_request = employee.submit(
                        reviewer, session, own_draft.id, own_draft.revision, "web"
                    )
                    session.commit()
                    assert review_service.get_review(
                        admin, session, own_request.id
                    ).items
                    assert reviewer.user_id not in reviewers_for(
                        session, org.id, manager.id
                    )
                    assert admin.user_id in reviewers_for(session, org.id, manager.id)
            for id_, before in evidence.items():
                assert session.get(Evidence, id_).model_dump() == before
    finally:
        db.rollback()
        for user_id in users:
            db.delete(db.get(User, user_id))
        db.commit()
