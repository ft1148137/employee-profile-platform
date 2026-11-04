"""Use employee email for login, preserving original identifiers for downgrade.

Revision ID: a84e001bc001
Revises: f61a901bc001
"""
from collections import defaultdict
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from email_validator import EmailNotValidError, validate_email

revision = "a84e001bc001"
down_revision = "f61a901bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user", sa.Column("legacy_username", sa.String(255), nullable=True))
    bind = op.get_bind()
    users = bind.execute(sa.text('SELECT id, username, email FROM "user"')).mappings().all()
    emails: dict[UUID, set[str]] = defaultdict(set)
    invalid: set[UUID] = set()
    for row in bind.execute(sa.text('''
        SELECT m.user_id, e.work_email FROM org_membership m
        JOIN employee e ON e.id = m.employee_id AND e.org_id = m.org_id
    ''')).mappings():
        email = (row["work_email"] or "").strip().lower()
        try:
            validate_email(email, check_deliverability=False)
        except EmailNotValidError:
            invalid.add(row["user_id"])
        emails[row["user_id"]].add(email)
    owners: dict[str, set[UUID]] = defaultdict(set)
    for user in users:
        for value in (user["username"], user["email"]):
            if value:
                owners[value.strip().lower()].add(user["id"])
    candidates = {id: next(iter(values)) for id, values in emails.items()
                  if len(values) == 1 and id not in invalid}
    for id, email in candidates.items():
        owners[email].add(id)
    for user in users:
        bind.execute(sa.text('UPDATE "user" SET legacy_username = :name WHERE id = :id'),
                     {"name": user["username"], "id": user["id"]})
        email = candidates.get(user["id"])
        if email and owners[email] == {user["id"]}:
            bind.execute(sa.text('UPDATE "user" SET username = :email, email = :email WHERE id = :id'),
                         {"email": email, "id": user["id"]})


def downgrade() -> None:
    # Restore each original identifier; uniqueness failures roll the migration back.
    op.execute('UPDATE "user" SET username = legacy_username WHERE legacy_username IS NOT NULL')
    op.drop_column("user", "legacy_username")
