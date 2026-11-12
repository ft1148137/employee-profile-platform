"""Keep one first-setup conversation per administrator."""

from alembic import op
import sqlalchemy as sa

revision = "b368001ss001"
down_revision = "b361001dc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("admin_assistant_session", sa.Column("purpose", sa.String(), nullable=True))
    op.create_check_constraint("ck_admin_session_purpose", "admin_assistant_session",
                               "purpose IS NULL OR purpose = 'setup'")
    op.create_index("uq_admin_session_setup", "admin_assistant_session",
                    ["org_id", "user_id"], unique=True,
                    postgresql_where=sa.text("purpose = 'setup'"))


def downgrade() -> None:
    # Conversations and messages survive as ordinary sessions; only purpose is lost.
    op.drop_index("uq_admin_session_setup", table_name="admin_assistant_session")
    op.drop_constraint("ck_admin_session_purpose", "admin_assistant_session", type_="check")
    op.drop_column("admin_assistant_session", "purpose")
