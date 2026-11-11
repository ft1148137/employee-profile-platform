"""Encrypted chat attachments for both private conversation types.

Revision ID: b313001ca001
Revises: b308001bc001
"""
from alembic import op
import sqlalchemy as sa

revision = "b313001ca001"
down_revision = "b308001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for prefix in ("bot", "admin_assistant"):
        table = f"{prefix}_attachment"
        op.create_table(
            table,
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("org_id", sa.Uuid(), sa.ForeignKey("organization.id"), nullable=False),
            sa.Column("session_id", sa.Uuid(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("filename", sa.String(), nullable=False),
            sa.Column("mime_type", sa.String(), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("ciphertext", sa.String(), nullable=False),
            sa.Column("text_ciphertext", sa.String(), nullable=True),
            sa.Column("notice", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(
                ["org_id", "session_id"],
                [f"{prefix}_session.org_id", f"{prefix}_session.id"],
                ondelete="CASCADE",
            ),
            sa.CheckConstraint("kind IN ('image', 'file')"),
            sa.CheckConstraint("size > 0"),
        )
        op.create_index(f"ix_{table}_org_id", table, ["org_id"])
        op.create_index(f"ix_{table}_session_time", table, ["org_id", "session_id", "created_at"])


def downgrade() -> None:
    # Downgrade permanently discards uploaded originals and extracted text.
    for prefix in ("admin_assistant", "bot"):
        op.drop_table(f"{prefix}_attachment")
