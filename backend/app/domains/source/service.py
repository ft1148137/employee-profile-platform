from datetime import UTC, datetime, time
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, col, select

from app.authz.principal import Principal
from app.connectors.jira.client import (
    CloudJiraClient,
    DataCenterJiraClient,
    JiraClient,
    RetryableSourceError,
    SourceAuthError,
    SourceUnreachable,
    jira_client_for,
)
from app.core.secrets import SecretUnavailable, encrypt_secret
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.models import Organization
from app.domains.source.models import SourceConnection, SyncRun
from app.domains.source.schedule import next_sync_time
from app.domains.source.schemas import (
    JiraConfig,
    JiraConnectionPublic,
    JiraConnectionTest,
    JiraConnectionUpdate,
    SyncRunsPublic,
)
from app.domains.source.sync import active_sync, public_run


class InvalidSourceSetting(Exception):
    pass


def read_jira(session: Session, org_id: UUID) -> SourceConnection | None:
    return session.exec(
        select(SourceConnection).where(
            SourceConnection.org_id == org_id, SourceConnection.kind == "jira"
        )
    ).first()


def public_jira(connection: SourceConnection | None) -> JiraConnectionPublic:
    if connection is None:
        return JiraConnectionPublic(configured=False)
    return JiraConnectionPublic(
        configured=True,
        config=JiraConfig.model_validate(connection.config),
        has_secret=bool(connection.secret_ciphertext),
        synced_until=connection.synced_until,
        sync_schedule=connection.sync_schedule,
        next_sync_at=connection.next_sync_at,
        updated_at=connection.updated_at,
    )


def save_jira(
    session: Session,
    principal: Principal,
    body: JiraConnectionUpdate,
    *,
    commit: bool = True,
) -> JiraConnectionPublic:
    assert principal.user_id is not None
    session.exec(
        select(Organization)
        .where(Organization.id == principal.org_id)
        .with_for_update()
    ).one()
    connection = read_jira(session, principal.org_id)
    if connection is not None:
        session.refresh(connection, with_for_update=True)
    if connection is not None and active_sync(session, connection):
        raise InvalidSourceSetting("同步进行中，请等完成后再修改 Jira 配置")
    config = body.model_dump(mode="json", exclude={"secret", "sync_schedule"})
    if connection is None:
        connection = SourceConnection(
            org_id=principal.org_id, kind="jira", updated_by_user_id=principal.user_id
        )
    destination_changed = any(
        config[k] != connection.config.get(k)
        for k in ("deployment", "auth_type", "base_url")
    )
    if body.secret is None and destination_changed:
        raise InvalidSourceSetting(
            "更换了 Jira 类型、认证方式或站点地址，请重新填写凭据"
        )
    if any(config[k] != connection.config.get(k) for k in ("deployment", "base_url")):
        connection.synced_until = None
    if body.secret is not None:
        connection.secret_ciphertext = encrypt_secret(body.secret.get_secret_value())
    if body.sync_schedule != connection.sync_schedule:
        connection.sync_schedule = body.sync_schedule
        connection.next_sync_at = next_sync_time(
            session, connection, body.sync_schedule
        )
    connection.config = config
    connection.updated_by_user_id = principal.user_id
    connection.updated_at = utc_now()
    session.add(connection)
    record_audit(
        session,
        org_id=principal.org_id,
        actor_type=principal.actor_type,
        actor_id=str(principal.user_id),
        action="source.jira_updated",
        target_type="source_connection",
        target_id=str(connection.id),
        details={
            "deployment": body.deployment,
            "hostname": urlsplit(body.base_url).hostname,
            "auth_type": body.auth_type,
            "sync_schedule": body.sync_schedule,
            "secret_changed": body.secret is not None,
        },
    )
    if commit:
        session.commit()
    else:
        session.flush()
    return public_jira(connection)


def read_sync_runs(
    session: Session,
    principal: Principal,
    skip: int = 0,
    limit: int = 100,
    kind: Literal["jira", "csv"] | None = None,
) -> SyncRunsPublic:
    query = (
        select(SyncRun, SourceConnection.kind)
        .join(
            SourceConnection,
            (col(SourceConnection.org_id) == SyncRun.org_id)
            & (col(SourceConnection.id) == SyncRun.connection_id),
        )
        .where(SyncRun.org_id == principal.org_id)
    )
    if kind is not None:
        query = query.where(SourceConnection.kind == kind)
    count = session.exec(select(func.count()).select_from(query.subquery())).one()
    rows = session.exec(
        query.order_by(col(SyncRun.created_at).desc(), col(SyncRun.id).desc())
        .offset(skip)
        .limit(limit)
    ).all()
    return SyncRunsPublic(
        data=[public_run(run, kind) for run, kind in rows], count=count
    )


def check_jira(session: Session, principal: Principal) -> JiraConnectionTest:
    connection = read_jira(session, principal.org_id)
    if connection is None:
        return JiraConnectionTest(ok=False, message="请先保存 Jira 配置")
    try:
        if not connection.secret_ciphertext:
            return JiraConnectionTest(ok=False, message="请先保存 Jira 凭据")
        config = JiraConfig.model_validate(connection.config)
        return _probe_jira(config, jira_client_for(connection))
    except SecretUnavailable as error:
        return JiraConnectionTest(ok=False, message=str(error))


def test_jira(config: JiraConfig, secret: str) -> JiraConnectionTest:
    client = CloudJiraClient if config.deployment == "cloud" else DataCenterJiraClient
    return _probe_jira(config, client(config, secret, None))


def _probe_jira(config: JiraConfig, client: JiraClient) -> JiraConnectionTest:
    try:
        with client as jira:
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
