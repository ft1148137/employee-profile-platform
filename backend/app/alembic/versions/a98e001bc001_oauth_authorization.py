"""OAuth clients, tenant-bound grants and rotating refresh tokens.

Revision ID: a98e001bc001
Revises: a95e001bc001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a98e001bc001"
down_revision = "a95e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "oauth_client",
        sa.Column("client_id", sa.String(), primary_key=True),
        sa.Column("client_metadata", postgresql.JSONB(), nullable=False),
        sa.Column("client_secret_encrypted", sa.String()),
        sa.Column("registered_ip", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oauth_client_registered_ip", "oauth_client", ["registered_ip"])
    op.create_table(
        "oauth_grant",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.String(), sa.ForeignKey("oauth_client.client_id"), nullable=False),
        sa.Column("redirect_uri", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("code_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("code_challenge", sa.String(), nullable=False),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("org_id", "id"),
        sa.ForeignKeyConstraint(["org_id", "user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.ForeignKeyConstraint(["org_id", "employee_id"], ["employee.org_id", "employee.id"]),
    )
    op.create_index("ix_oauth_grant_org_id", "oauth_grant", ["org_id"])
    op.create_table(
        "oauth_refresh_token",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("grant_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["org_id", "grant_id"], ["oauth_grant.org_id", "oauth_grant.id"]),
    )
    op.create_index("ix_oauth_refresh_token_org_id", "oauth_refresh_token", ["org_id"])
    op.add_column("personal_access_token", sa.Column("oauth_grant_id", sa.Uuid()))
    op.create_foreign_key("fk_pat_oauth_grant", "personal_access_token", "oauth_grant", ["org_id", "oauth_grant_id"], ["org_id", "id"])
    op.create_check_constraint("ck_pat_oauth_grant", "personal_access_token", "(client_kind = 'oauth') = (oauth_grant_id IS NOT NULL)")


def downgrade() -> None:
    # Downgrading removes grants; remove OAuth credentials while keeping personal tokens.
    op.execute(sa.text("DELETE FROM personal_access_token WHERE client_kind = 'oauth'"))
    op.drop_constraint("ck_pat_oauth_grant", "personal_access_token", type_="check")
    op.drop_constraint("fk_pat_oauth_grant", "personal_access_token", type_="foreignkey")
    op.drop_column("personal_access_token", "oauth_grant_id")
    op.drop_table("oauth_refresh_token")
    op.drop_table("oauth_grant")
    op.drop_table("oauth_client")
