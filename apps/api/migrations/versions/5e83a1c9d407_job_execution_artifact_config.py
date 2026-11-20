"""Persist job execution and artifact configuration.

Revision ID: 5e83a1c9d407
Revises: d19a3c7b4e62
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5e83a1c9d407"
down_revision: str | None = "d19a3c7b4e62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_jobs_nonterminal_created", table_name="jobs")
    op.add_column(
        "jobs",
        sa.Column(
            "execution_config",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "artifact_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_jobs_nonterminal_created",
        "jobs",
        ["tenant_id", "created_at"],
        unique=False,
        postgresql_where=sa.text(
            "(status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELED', 'CANCELLED', 'TIMED_OUT'))"
        ),
    )


def downgrade() -> None:
    # Dropping these columns loses submitted execution/artifact configuration.
    op.drop_index("ix_jobs_nonterminal_created", table_name="jobs")
    op.drop_column("jobs", "artifact_policy")
    op.drop_column("jobs", "execution_config")
    op.create_index(
        "ix_jobs_nonterminal_created",
        "jobs",
        ["tenant_id", "created_at"],
        unique=False,
        postgresql_where=sa.text(
            "(status NOT IN ('SUCCEEDED', 'FAILED', 'CANCELED', 'TIMED_OUT'))"
        ),
    )
