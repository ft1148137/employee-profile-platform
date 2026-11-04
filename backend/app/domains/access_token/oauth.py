import hashlib
import secrets
from datetime import timedelta
from uuid import UUID

from fastapi import HTTPException
from mcp.server.auth.provider import TokenError
from mcp.shared.auth import OAuthToken
from sqlmodel import Session, col, delete, func, select, update

from app.domains.access_token.auth import credential_principal
from app.domains.access_token.models import (
    OAuthClient,
    OAuthGrant,
    OAuthRefreshToken,
    PersonalAccessToken,
)
from app.domains.audit.models import ActorKind
from app.domains.audit.service import record_audit
from app.domains.base import utc_now


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def revoke_grant(session: Session, grant: OAuthGrant) -> None:
    now = utc_now()
    grant.revoked_at = now
    session.add(grant)
    session.exec(
        update(PersonalAccessToken)
        .where(
            col(PersonalAccessToken.org_id) == grant.org_id,
            col(PersonalAccessToken.oauth_grant_id) == grant.id,
            col(PersonalAccessToken.revoked_at).is_(None),
        )
        .values(revoked_at=now)
    )
    session.exec(
        update(OAuthRefreshToken)
        .where(
            col(OAuthRefreshToken.org_id) == grant.org_id,
            col(OAuthRefreshToken.grant_id) == grant.id,
            col(OAuthRefreshToken.used_at).is_(None),
        )
        .values(used_at=now, expires_at=now)
    )
    record_audit(
        session,
        org_id=grant.org_id,
        actor_type=ActorKind.USER,
        actor_id=str(grant.user_id),
        action="oauth.revoked",
        target_type="oauth_grant",
        target_id=str(grant.id),
        details={},
    )


def exchange(
    session: Session, client_id: str, secret: str, *, refresh: bool
) -> OAuthToken:
    if refresh:
        row = session.exec(
            select(OAuthRefreshToken)
            .join(
                OAuthGrant,
                (col(OAuthGrant.org_id) == col(OAuthRefreshToken.org_id))
                & (col(OAuthGrant.id) == col(OAuthRefreshToken.grant_id)),
            )
            .where(
                col(OAuthRefreshToken.token_hash) == digest(secret),
                col(OAuthGrant.client_id) == client_id,
            )
        ).one_or_none()
        grant_id = row.grant_id if row else None
    else:
        row = None
        grant_id = session.exec(
            select(col(OAuthGrant.id)).where(
                col(OAuthGrant.code_hash) == digest(secret),
                col(OAuthGrant.client_id) == client_id,
            )
        ).one_or_none()
    if grant_id is None:
        raise TokenError("invalid_grant")
    # Serialize rotation, code exchange and revocation on the same grant.
    grant = session.exec(
        select(OAuthGrant)
        .where(col(OAuthGrant.id) == grant_id, col(OAuthGrant.client_id) == client_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if grant.revoked_at:
        raise TokenError("invalid_grant")
    if row is not None:
        session.refresh(row)
    if (row.used_at if row else grant.code_used_at) is not None:
        revoke_grant(session, grant)
        session.commit()  # Revocation must survive the invalid_grant response.
        raise TokenError("invalid_grant")
    client = session.get(OAuthClient, client_id)
    assert client is not None
    try:
        credential_principal(
            session,
            grant.org_id,
            grant.user_id,
            grant.employee_id,
            grant.created_at,
            client.client_metadata["client_name"],
        )
    except HTTPException:
        raise TokenError("invalid_grant") from None
    now = utc_now()
    if refresh:
        assert row is not None
        consumed = session.exec(
            update(OAuthRefreshToken)
            .where(
                col(OAuthRefreshToken.org_id) == grant.org_id,
                col(OAuthRefreshToken.id) == row.id,
                col(OAuthRefreshToken.used_at).is_(None),
                col(OAuthRefreshToken.expires_at) > now,
            )
            .values(used_at=now)
            .returning(col(OAuthRefreshToken.id))
        ).one_or_none()
    else:
        consumed = session.exec(
            update(OAuthGrant)
            .where(
                col(OAuthGrant.org_id) == grant.org_id,
                col(OAuthGrant.id) == grant.id,
                col(OAuthGrant.code_used_at).is_(None),
                col(OAuthGrant.code_expires_at) > now,
                col(OAuthGrant.revoked_at).is_(None),
            )
            .values(code_used_at=now)
            .returning(col(OAuthGrant.id))
        ).one_or_none()
    if consumed is None:
        raise TokenError("invalid_grant")
    # Carry grant activity across expiry cleanup and refresh-token rotation.
    last_used_at = session.exec(
        select(func.max(PersonalAccessToken.last_used_at)).where(
            PersonalAccessToken.org_id == grant.org_id,
            PersonalAccessToken.oauth_grant_id == grant.id,
        )
    ).one()
    session.exec(
        delete(PersonalAccessToken).where(
            col(PersonalAccessToken.org_id) == grant.org_id,
            col(PersonalAccessToken.oauth_grant_id) == grant.id,
            col(PersonalAccessToken.expires_at) <= now,
        )
    )
    access, renewal = "epp_" + secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    session.add(
        PersonalAccessToken(
            org_id=grant.org_id,
            user_id=grant.user_id,
            employee_id=grant.employee_id,
            oauth_grant_id=grant.id,
            last_used_at=last_used_at,
            name=client.client_metadata["client_name"],
            client_kind="oauth",
            token_hash=digest(access),
            prefix=access[:12],
            expires_at=now + timedelta(hours=1),
        )
    )
    session.add(
        OAuthRefreshToken(
            org_id=grant.org_id,
            grant_id=grant.id,
            token_hash=digest(renewal),
            expires_at=now + timedelta(days=30),
        )
    )
    return OAuthToken(
        access_token=access, refresh_token=renewal, expires_in=3600, scope="employee"
    )


def revoke_by_id(session: Session, org_id: UUID, grant_id: UUID) -> None:
    grant = session.exec(
        select(OAuthGrant)
        .where(col(OAuthGrant.org_id) == org_id, col(OAuthGrant.id) == grant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if grant.revoked_at is None:
        revoke_grant(session, grant)
