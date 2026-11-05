"""Share authenticated IM heartbeat status between the gateway and API."""
from alembic import op
import sqlalchemy as sa

revision = "b129001bc001"
down_revision = "b130001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("im_setting", sa.Column("connection_checked_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.drop_column("im_setting", "connection_checked_at")
