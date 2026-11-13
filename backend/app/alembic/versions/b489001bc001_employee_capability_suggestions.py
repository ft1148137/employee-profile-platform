"""Record the employee suggesting a capability."""

import sqlalchemy as sa
from alembic import op

revision = "b489001bc001"
down_revision = "b381001dc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "capability_proposal",
        sa.Column("proposed_by_employee_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_proposal_employee",
        "capability_proposal",
        "employee",
        ["org_id", "proposed_by_employee_id"],
        ["org_id", "id"],
    )


def downgrade() -> None:
    # Suggestions remain, but their employee attribution is lost on downgrade.
    op.drop_constraint(
        "fk_proposal_employee", "capability_proposal", type_="foreignkey"
    )
    op.drop_column("capability_proposal", "proposed_by_employee_id")
