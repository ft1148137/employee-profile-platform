"""Use an IANA timezone per organization, defaulting existing tenants to Beijing."""

import sqlalchemy as sa
from alembic import op

revision = "b617001dc001"
down_revision = "b615001dc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organization",
        sa.Column(
            "timezone", sa.String(), nullable=False, server_default="Asia/Shanghai"
        ),
    )


def downgrade() -> None:
    op.drop_column("organization", "timezone")
