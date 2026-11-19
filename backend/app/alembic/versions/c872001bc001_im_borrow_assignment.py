"""Retain the original assignment for borrow notification authorization."""

import sqlalchemy as sa
from alembic import op

revision = "c872001bc001"
down_revision = "c840001bc001"
branch_labels = None
depends_on = None

TABLE = "im_notification"
COLUMN = "borrow_assignment_id"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.Uuid(), nullable=True))


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE im_notification IN ACCESS EXCLUSIVE MODE"))
    if connection.execute(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM im_notification"
            " WHERE status = 'pending' AND borrow_assignment_id IS NOT NULL)"
        )
    ).scalar_one():
        raise RuntimeError(
            "存在待发借调 IM 提醒，无法降级并保留分配来源。"
            "请等待提醒发送完成，或在站内收件箱处理后等待待发状态解除；"
            "也可按部署文档使用升级前备份回退。"
        )
    op.drop_column(TABLE, COLUMN)
