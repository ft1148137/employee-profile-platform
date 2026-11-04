from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel

from app.domains.base import TenantRecord, tenant_fk, utc_now


class OAuthClient(SQLModel, table=True):
    __tablename__ = "oauth_client"
    client_id: str = Field(primary_key=True)
    client_metadata: dict[str, Any] = Field(sa_type=JSONB)
    client_secret_encrypted: str | None = Field(default=None, repr=False)
    registered_ip: str = Field(index=True)
    created_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )


class OAuthGrant(TenantRecord, table=True):
    __tablename__ = "oauth_grant"
    __table_args__ = (
        UniqueConstraint("org_id", "id"),
        tenant_fk("employee_id", "employee"),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
    )
    user_id: UUID
    employee_id: UUID
    client_id: str = Field(foreign_key="oauth_client.client_id")
    redirect_uri: str
    resource: str
    code_hash: str = Field(unique=True, max_length=64, repr=False)
    code_challenge: str
    code_expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    code_used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class OAuthRefreshToken(TenantRecord, table=True):
    __tablename__ = "oauth_refresh_token"
    __table_args__ = (tenant_fk("grant_id", "oauth_grant"),)
    grant_id: UUID
    token_hash: str = Field(unique=True, max_length=64, repr=False)
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class PersonalAccessToken(TenantRecord, table=True):
    __tablename__ = "personal_access_token"
    __table_args__ = (
        tenant_fk("employee_id", "employee"),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
        CheckConstraint("scopes = 'employee'", name="ck_pat_scopes"),
        CheckConstraint("client_kind IN ('pat', 'oauth')", name="ck_pat_client_kind"),
        tenant_fk("oauth_grant_id", "oauth_grant"),
        CheckConstraint(
            "(client_kind = 'oauth') = (oauth_grant_id IS NOT NULL)",
            name="ck_pat_oauth_grant",
        ),
    )
    user_id: UUID
    employee_id: UUID
    name: str = Field(max_length=100)
    token_hash: str = Field(unique=True, max_length=64)
    prefix: str = Field(max_length=16)
    scopes: str = "employee"
    client_kind: str = "pat"
    oauth_grant_id: UUID | None = None
    last_used_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))
    expires_at: datetime = Field(sa_type=DateTime(timezone=True))
    revoked_at: datetime | None = Field(default=None, sa_type=DateTime(timezone=True))


class McpSetting(SQLModel, table=True):
    __tablename__ = "mcp_setting"
    __table_args__ = (
        ForeignKeyConstraint(
            ["org_id", "updated_by"],
            ["org_membership.org_id", "org_membership.user_id"],
        ),
    )
    org_id: UUID = Field(primary_key=True, foreign_key="organization.id")
    enabled: bool = False
    allowed_clients: list[str] = Field(default_factory=list, sa_type=JSONB)
    updated_by: UUID
    updated_at: datetime = Field(
        default_factory=utc_now, sa_type=DateTime(timezone=True)
    )
