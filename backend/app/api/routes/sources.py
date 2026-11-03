from datetime import UTC, datetime, time

from fastapi import APIRouter, HTTPException

from app.api.deps import SessionDep
from app.api.secret_safe import SecretSafeRoute
from app.authz.deps import OrgAdminDep
from app.connectors.jira.client import (
    RetryableSourceError,
    SourceAuthError,
    SourceUnreachable,
    jira_client_for,
)
from app.core.secrets import SecretUnavailable
from app.domains.source.schemas import (
    JiraConfig,
    JiraConnectionPublic,
    JiraConnectionTest,
    JiraConnectionUpdate,
)
from app.domains.source.service import (
    InvalidSourceSetting,
    public_jira,
    read_jira,
    save_jira,
)

router = APIRouter(
    prefix="/orgs/{org_id}/sources",
    tags=["sources"],
    route_class=SecretSafeRoute,
)


@router.get(
    "/jira", response_model=JiraConnectionPublic, response_model_exclude_unset=True
)
def get_jira(session: SessionDep, principal: OrgAdminDep) -> JiraConnectionPublic:
    return public_jira(read_jira(session, principal.org_id))


@router.put("/jira", response_model=JiraConnectionPublic)
def put_jira(
    body: JiraConnectionUpdate,
    session: SessionDep,
    principal: OrgAdminDep,
) -> JiraConnectionPublic:
    try:
        return save_jira(session, principal, body)
    except InvalidSourceSetting as error:
        raise HTTPException(422, str(error)) from None


@router.post("/jira/test", response_model=JiraConnectionTest)
def test_jira(session: SessionDep, principal: OrgAdminDep) -> JiraConnectionTest:
    connection = read_jira(session, principal.org_id)
    if connection is None:
        return JiraConnectionTest(ok=False, message="请先保存 Jira 配置")
    try:
        config = JiraConfig.model_validate(connection.config)
        with jira_client_for(connection) as jira:
            account = jira.current_account()
            jira.search(datetime.combine(config.start_date, time.min, UTC), None, 1)
    except (
        SourceAuthError,
        SourceUnreachable,
        RetryableSourceError,
        SecretUnavailable,
    ) as error:
        return JiraConnectionTest(ok=False, message=str(error))
    return JiraConnectionTest(ok=True, message="Jira 连接成功", account_name=account)
