"""Daily administrator reminders, without employee details."""
from alembic import op
import sqlalchemy as sa

revision = "b283001bc001"
down_revision = "b286001bc001"
branch_labels = None
depends_on = None

KINDS = "'initial_review','submitted','status_changed','question','dictionary_changed','review_requested','decided'"


def upgrade() -> None:
    op.add_column("inbox_item", sa.Column("admin_todo_date", sa.Date()))
    op.add_column("inbox_item", sa.Column("admin_todo_count", sa.Integer()))
    op.create_unique_constraint("uq_inbox_admin_todo_day", "inbox_item", ["org_id", "recipient_user_id", "admin_todo_date"])
    op.drop_constraint("inbox_item_kind_check", "inbox_item")
    op.create_check_constraint("inbox_item_kind_check", "inbox_item", f"kind IN ({KINDS},'admin_todos')")


def downgrade() -> None:
    op.execute("DELETE FROM im_notification WHERE (org_id, inbox_item_id) IN (SELECT org_id, id FROM inbox_item WHERE kind='admin_todos')")
    op.execute("DELETE FROM inbox_item WHERE kind='admin_todos'")
    op.drop_constraint("inbox_item_kind_check", "inbox_item")
    op.create_check_constraint("inbox_item_kind_check", "inbox_item", f"kind IN ({KINDS})")
    op.drop_constraint("uq_inbox_admin_todo_day", "inbox_item")
    op.drop_column("inbox_item", "admin_todo_count")
    op.drop_column("inbox_item", "admin_todo_date")
