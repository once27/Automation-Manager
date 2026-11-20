"""Add stable workflow family identity for immutable version rows.

Revision ID: d19a3c7b4e62
Revises: 8a62639b6233
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d19a3c7b4e62"
down_revision: str | None = "8a62639b6233"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_families",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_workflow_families_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "name", name="uq_workflow_families_tenant_name"
        ),
    )

    op.add_column(
        "workflows", sa.Column("workflow_family_id", sa.Uuid(), nullable=True)
    )

    # Preserve every existing version-row ID; only introduce a family parent.
    # The family's initial enabled value reflects whether any version is enabled.
    op.execute(
        sa.text(
            """
            INSERT INTO workflow_families
                (id, tenant_id, name, enabled, created_at)
            SELECT gen_random_uuid(), tenant_id, name, bool_or(enabled), min(created_at)
            FROM workflows
            GROUP BY tenant_id, name
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE workflows AS version
            SET workflow_family_id = family.id
            FROM workflow_families AS family
            WHERE family.tenant_id = version.tenant_id
              AND family.name = version.name
            """
        )
    )

    op.alter_column("workflows", "workflow_family_id", nullable=False)
    op.create_foreign_key(
        "fk_workflows_tenant_family",
        "workflows",
        "workflow_families",
        ["tenant_id", "workflow_family_id"],
        ["tenant_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_workflows_tenant_family_version",
        "workflows",
        ["tenant_id", "workflow_family_id", "version"],
    )


def downgrade() -> None:
    # Downgrade discards stable family IDs and family-level enabled state.
    # Re-upgrading would create new family IDs and infer enabled state from
    # version rows, so this is not a lossless rollback after registry use.
    op.drop_constraint(
        "uq_workflows_tenant_family_version", "workflows", type_="unique"
    )
    op.drop_constraint("fk_workflows_tenant_family", "workflows", type_="foreignkey")
    op.drop_column("workflows", "workflow_family_id")
    op.drop_table("workflow_families")
