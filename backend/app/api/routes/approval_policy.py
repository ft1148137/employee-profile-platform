from fastapi import APIRouter

from app.api.deps import SessionDep
from app.authz.deps import OrgAdminDep
from app.domains.review.policy import PolicyInput, PolicyView, policy_view, save_policy

router = APIRouter(prefix="/orgs/{org_id}/approval-policy", tags=["approval-policy"])


@router.get("")
def get_approval_policy(session: SessionDep, principal: OrgAdminDep) -> PolicyView:
    return policy_view(session, principal.org_id)


@router.put("")
def put_approval_policy(
    body: PolicyInput, session: SessionDep, principal: OrgAdminDep
) -> PolicyView:
    return save_policy(session, principal, body)
