"""Use deployment-wide employee usernames and optional email addresses.

Revision ID: a51b093e72c4
Revises: 7d3e0f1a5b92
"""
from alembic import op
import sqlalchemy as sa

revision = "a51b093e72c4"
down_revision = "7d3e0f1a5b92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("username", sa.String(255), nullable=True))
    # A platform user may belong to several organizations; never choose an arbitrary code.
    ambiguous = op.get_bind().execute(sa.text('''
        SELECT m.user_id FROM org_membership m
        JOIN employee e ON e.org_id = m.org_id AND e.id = m.employee_id
        WHERE NULLIF(trim(e.employee_code), '') IS NOT NULL
        GROUP BY m.user_id HAVING count(DISTINCT trim(e.employee_code)) > 1
    ''')).first()
    if ambiguous:
        raise ValueError("A user has multiple employee codes; resolve these before upgrading")
    op.execute('''
        UPDATE "user" u SET username = COALESCE((
            SELECT min(trim(e.employee_code)) FROM org_membership m
            JOIN employee e ON e.org_id = m.org_id AND e.id = m.employee_id
            WHERE m.user_id = u.id AND NULLIF(trim(e.employee_code), '') IS NOT NULL
        ), trim(u.email))
    ''')
    op.alter_column("user", "username", nullable=False)
    op.drop_index("ix_user_email", table_name="user")
    op.alter_column("user", "email", nullable=True)
    op.create_index("ix_user_username_lower", "user", [sa.text("lower(username)")], unique=True)
    op.create_index("ix_user_email_lower", "user", [sa.text("lower(email)")], unique=True,
                    postgresql_where=sa.text("email IS NOT NULL"))
    op.add_column("user", sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("user", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if op.get_bind().execute(sa.text('SELECT id FROM "user" WHERE email IS NULL LIMIT 1')).first():
        raise ValueError("Cannot downgrade: users have no email; fill every email before downgrading")
    op.drop_column("user", "last_login_at")
    op.drop_column("user", "password_changed_at")
    op.drop_column("user", "must_change_password")
    op.drop_index("ix_user_email_lower", table_name="user")
    op.drop_index("ix_user_username_lower", table_name="user")
    op.alter_column("user", "email", nullable=False)
    op.create_index("ix_user_email", "user", ["email"], unique=True)
    op.drop_column("user", "username")
