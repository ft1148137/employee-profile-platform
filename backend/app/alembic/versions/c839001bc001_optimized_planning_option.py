"""Allow the optimized Planning option kind."""

import sqlalchemy as sa
from alembic import op

revision = "c839001bc001"
down_revision = "c837001bc001"
branch_labels = None
depends_on = None

TABLE = "planning_option"
CONSTRAINT = "planning_option_kind_check"
PREVIOUS = "'direct', 'newcomer', 'borrow', 'delay', 'reduce_scope', 'phased', 'custom'"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(
        CONSTRAINT, TABLE, f"kind IN ({PREVIOUS}, 'optimized')"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE planning_option IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM planning_option WHERE kind = 'optimized')")
    ).scalar_one():
        raise RuntimeError("存在优化方案，无法降级并保留数据")
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.create_check_constraint(CONSTRAINT, TABLE, f"kind IN ({PREVIOUS})")
