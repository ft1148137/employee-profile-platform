"""Add the assignment feedback profile request origin."""

import sqlalchemy as sa
from alembic import op

revision = "c837001bc001"
down_revision = "c836001bc001"
branch_labels = None
depends_on = None

TABLE = "profile_change_request"
CONSTRAINT = "profile_change_request_origin_check"
PREVIOUS = "'initial_sync', 'incremental_sync', 'conversation', 'manual', 'correction', 'import'"


def upgrade() -> None:
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.alter_column(TABLE, "origin", type_=sa.String(19))
    op.create_check_constraint(
        CONSTRAINT, TABLE, f"origin IN ({PREVIOUS}, 'assignment_feedback')"
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text("LOCK TABLE profile_change_request IN ACCESS EXCLUSIVE MODE")
    )
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM profile_change_request WHERE origin = 'assignment_feedback')"
        )
    ).scalar_one():
        raise RuntimeError("存在工作反馈请求，无法降级并保留数据")
    op.drop_constraint(CONSTRAINT, TABLE, type_="check")
    op.alter_column(TABLE, "origin", type_=sa.String(16))
    op.create_check_constraint(CONSTRAINT, TABLE, f"origin IN ({PREVIOUS})")
