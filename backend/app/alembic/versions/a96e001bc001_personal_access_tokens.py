"""Enterprise MCP settings and employee personal access tokens.

Revision ID: a96e001bc001
Revises: b97e001bc001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a96e001bc001"
down_revision = "b97e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_setting",
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("allowed_clients", postgresql.JSONB(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["org_id", "updated_by"], ["org_membership.org_id", "org_membership.user_id"]),
    )
    op.create_table(
        "personal_access_token",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("scopes", sa.String(), nullable=False),
        sa.Column("client_kind", sa.String(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["org_id", "employee_id"], ["employee.org_id", "employee.id"]),
        sa.ForeignKeyConstraint(["org_id", "user_id"], ["org_membership.org_id", "org_membership.user_id"]),
        sa.CheckConstraint("scopes = 'employee'", name="ck_pat_scopes"),
        sa.CheckConstraint("client_kind IN ('pat', 'oauth')", name="ck_pat_client_kind"),
    )
    op.create_index("ix_personal_access_token_org_id", "personal_access_token", ["org_id"])


def downgrade() -> None:
    op.drop_table("personal_access_token")
    op.drop_table("mcp_setting")
