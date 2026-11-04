"""Allow dictionary change notifications.

Revision ID: a91e001bc001
Revises: a89e001bc001
"""
from alembic import op

revision = "a91e001bc001"
down_revision = "a89e001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE inbox_item DROP CONSTRAINT inbox_item_kind_check;
        ALTER TABLE inbox_item ALTER COLUMN kind TYPE varchar(18);
        ALTER TABLE inbox_item ADD CONSTRAINT inbox_item_kind_check
            CHECK (kind IN ('initial_review','submitted','status_changed','question','dictionary_changed'));
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM inbox_item WHERE kind='dictionary_changed';
        ALTER TABLE inbox_item DROP CONSTRAINT inbox_item_kind_check;
        ALTER TABLE inbox_item ALTER COLUMN kind TYPE varchar(14);
        ALTER TABLE inbox_item ADD CONSTRAINT inbox_item_kind_check
            CHECK (kind IN ('initial_review','submitted','status_changed','question'));
    """)
