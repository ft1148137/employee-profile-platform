from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.deps import SessionDep
from app.authz.deps import OrgPrincipalDep
from app.domains.account import service
from app.domains.account.export import export_accounts
from app.domains.account.schemas import (
    AccountsPublic,
    AccountStatus,
    InitialPassword,
    IssueAccounts,
    UnlinkedAccountsPublic,
)
from app.domains.base import utc_now
from app.domains.org.models import EmploymentStatus
from app.models import Message

router = APIRouter(prefix="/orgs/{org_id}/accounts", tags=["accounts"])


@router.get("", response_model=AccountsPublic)
def read_accounts(
    session: SessionDep,
    principal: OrgPrincipalDep,
    department_id: UUID | None = None,
    status: AccountStatus | None = None,
    q: str | None = None,
    employment_status: EmploymentStatus | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
    employee_id: UUID | None = None,
) -> AccountsPublic:
    return service.list_accounts(
        session,
        principal,
        department_id,
        status,
        q,
        skip,
        limit,
        employment_status,
        employee_id=employee_id,
    )


@router.get("/unlinked", response_model=UnlinkedAccountsPublic)
def read_unlinked_accounts(
    session: SessionDep,
    principal: OrgPrincipalDep,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=100),
) -> UnlinkedAccountsPublic:
    return service.list_unlinked_accounts(session, principal, skip, limit)


@router.post(
    "/issue",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {
                    "schema": {"type": "string", "format": "binary"}
                }
            }
        }
    },
)
def issue_accounts(
    body: IssueAccounts, session: SessionDep, principal: OrgPrincipalDep
) -> Response:
    org, rows = service.issue_accounts(session, principal, body)
    content = export_accounts(rows)
    filename = quote(f"账号-{org.name}-{utc_now():%Y%m%d-%H%M}.xlsx", safe="")
    response = Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
            "X-Issued-Count": str(len(rows)),
        },
    )
    session.commit()
    return response


@router.post("/{user_id}/reset-password", response_model=InitialPassword)
def reset_password(
    user_id: UUID, session: SessionDep, principal: OrgPrincipalDep, response: Response
) -> InitialPassword:
    response.headers["Cache-Control"] = "no-store"
    result = service.change_account(session, principal, user_id, "password_reset")
    assert result is not None
    return result


@router.post("/{user_id}/disable", response_model=Message)
def disable_account(
    user_id: UUID, session: SessionDep, principal: OrgPrincipalDep
) -> Message:
    service.change_account(session, principal, user_id, "disabled")
    return Message(message="账号已停用")


@router.post("/{user_id}/enable", response_model=Message)
def enable_account(
    user_id: UUID, session: SessionDep, principal: OrgPrincipalDep
) -> Message:
    service.change_account(session, principal, user_id, "enabled")
    return Message(message="账号已启用")
