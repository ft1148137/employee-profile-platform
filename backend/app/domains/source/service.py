from urllib.parse import urlsplit
from uuid import UUID

from sqlmodel import Session, select

from app.authz.principal import Principal
from app.core.secrets import encrypt_secret
from app.domains.audit.service import record_audit
from app.domains.base import utc_now
from app.domains.org.models import Organization
from app.domains.source.models import SourceConnection
from app.domains.source.schedule import next_sync_time
from app.domains.source.schemas import (
    JiraConfig,
    JiraConnectionPublic,
    JiraConnectionUpdate,
)
from app.domains.source.sync import active_sync


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
    session: Session, principal: Principal, body: JiraConnectionUpdate
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
    session.commit()
    return public_jira(connection)
