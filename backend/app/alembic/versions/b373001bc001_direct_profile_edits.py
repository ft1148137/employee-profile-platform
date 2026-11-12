"""Allow direct profile decisions and employee notifications."""
from alembic import op

revision = "b373001bc001"
down_revision = "b368001ss001"
branch_labels = None
depends_on = None

VIA = "'web','mcp','im','policy','sync'"
KINDS = "'initial_review','submitted','status_changed','question','dictionary_changed','review_requested','decided','admin_todos'"


def upgrade() -> None:
    op.drop_constraint("review_decision_via_check", "review_decision")
    op.create_check_constraint("review_decision_via_check", "review_decision", f"via IN ({VIA},'direct')")
    op.drop_constraint("inbox_item_kind_check", "inbox_item")
    op.create_check_constraint("inbox_item_kind_check", "inbox_item", f"kind IN ({KINDS},'profile_edited')")


def downgrade() -> None:
    # Preserve records but lose the direct-edit distinction on downgrade.
    op.execute("UPDATE review_decision SET via='web' WHERE via='direct'")
    op.execute("UPDATE inbox_item SET kind='status_changed' WHERE kind='profile_edited'")
    op.drop_constraint("review_decision_via_check", "review_decision")
    op.create_check_constraint("review_decision_via_check", "review_decision", f"via IN ({VIA})")
    op.drop_constraint("inbox_item_kind_check", "inbox_item")
    op.create_check_constraint("inbox_item_kind_check", "inbox_item", f"kind IN ({KINDS})")
