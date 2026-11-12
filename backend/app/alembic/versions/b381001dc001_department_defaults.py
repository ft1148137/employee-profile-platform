"""Track Pi-generated department defaults and protect existing manual settings."""

from alembic import op
import sqlalchemy as sa

revision = "b381001dc001"
down_revision = "b373001bc001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("department_capability", "created_by_user_id", nullable=True)
    op.add_column("department", sa.Column("capability_source", sa.String(), nullable=True))
    op.add_column("department", sa.Column("capability_schema_version_id", sa.Uuid(), nullable=True))
    op.create_check_constraint("ck_department_capability_source", "department", "capability_source IN ('pi', 'admin')")
    op.create_foreign_key(
        "fk_department_capability_schema", "department", "capability_schema_version",
        ["org_id", "capability_schema_version_id"], ["org_id", "id"],
    )
    # Preserve manual assignments, including departments previously cleared by an admin.
    op.execute("""
        UPDATE department AS d SET capability_source = 'admin'
        WHERE EXISTS (
            SELECT 1 FROM department_capability AS c
            WHERE c.org_id = d.org_id AND c.department_id = d.id
        ) OR EXISTS (
            SELECT 1 FROM audit_event AS a
            WHERE a.org_id = d.org_id AND a.target_type = 'department'
              AND a.target_id = d.id::text AND a.action = 'department.capabilities_updated'
        )
    """)


def downgrade() -> None:
    # Downgrade loses Pi-generated assignments and all source/version markers; audits remain.
    op.execute("DELETE FROM department_capability WHERE created_by_user_id IS NULL")
    op.drop_constraint("fk_department_capability_schema", "department", type_="foreignkey")
    op.drop_constraint("ck_department_capability_source", "department", type_="check")
    op.drop_column("department", "capability_schema_version_id")
    op.drop_column("department", "capability_source")
    op.alter_column("department_capability", "created_by_user_id", nullable=False)
