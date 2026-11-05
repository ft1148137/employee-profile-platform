"""IM bindings, one-time codes and event deduplication."""
from alembic import op
import sqlalchemy as sa

revision = "b125001bc001"
down_revision = "b117002bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("im_setting", sa.Column("bot_identity", sa.String()))
    for name, columns, constraints in [
        ("im_binding", [
            sa.Column("platform", sa.String(), nullable=False),
            sa.Column("external_user_id", sa.String(), nullable=False),
            sa.Column("external_chat_id", sa.String(), nullable=False),
            sa.Column("bot_identity", sa.String(), nullable=False),
            sa.Column("bound_via", sa.String(), nullable=False),
            sa.Column("notify", sa.Boolean(), nullable=False),
        ], [
            sa.UniqueConstraint("org_id", "platform", "external_user_id"),
            sa.UniqueConstraint("org_id", "platform", "employee_id"),
            sa.CheckConstraint("platform IN ('telegram', 'feishu', 'wecom')"),
            sa.CheckConstraint("bound_via IN ('code', 'email')"),
        ]),
        ("im_bind_code", [
            sa.Column("code_hash", sa.String(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True)),
        ], [sa.UniqueConstraint("org_id", "code_hash")]),
    ]:
        op.create_table(name,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
            sa.Column("employee_id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            *columns, *constraints,
            sa.ForeignKeyConstraint(["org_id", "employee_id"], ["employee.org_id", "employee.id"]),
        )
        op.create_index(f"ix_{name}_org_id", name, ["org_id"])
    op.create_table("im_event",
        sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), primary_key=True),
        sa.Column("platform", sa.String(), primary_key=True),
        sa.Column("event_id", sa.String(), primary_key=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("im_event")
    op.drop_table("im_bind_code")
    op.drop_table("im_binding")
    op.drop_column("im_setting", "bot_identity")
