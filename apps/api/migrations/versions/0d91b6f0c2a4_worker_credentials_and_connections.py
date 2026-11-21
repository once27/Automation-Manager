"""Add separate worker credentials and connection ownership.

Revision ID: 0d91b6f0c2a4
Revises: 5e83a1c9d407
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0d91b6f0c2a4"
down_revision: str | None = "5e83a1c9d407"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKER_STATUSES = (
    "REGISTERING",
    "ONLINE",
    "BUSY",
    "DRAINING",
    "OFFLINE",
    "DISABLED",
    "ERROR",
)


def upgrade() -> None:
    # Earlier phases persisted a lowercase default without a status constraint.
    # Treat any legacy or unrecognized value as conservatively unavailable
    # before enforcing the protocol's canonical status vocabulary.
    op.execute(
        sa.text(
            """
            UPDATE workers
            SET status = CASE
                WHEN upper(status) IN (
                    'REGISTERING', 'ONLINE', 'BUSY', 'DRAINING',
                    'OFFLINE', 'DISABLED', 'ERROR'
                ) THEN upper(status)
                ELSE 'OFFLINE'
            END
            """
        )
    )
    op.alter_column(
        "workers",
        "status",
        existing_type=sa.String(length=30),
        server_default="OFFLINE",
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_workers_status",
        "workers",
        "status IN ('REGISTERING', 'ONLINE', 'BUSY', 'DRAINING', "
        "'OFFLINE', 'DISABLED', 'ERROR')",
    )
    op.add_column("workers", sa.Column("connection_id", sa.Uuid(), nullable=True))

    op.create_table(
        "worker_credentials",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("secret_hash", sa.Text(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(key_prefix) BETWEEN 1 AND 16",
            name="ck_worker_credentials_prefix_length",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "worker_id"],
            ["workers.tenant_id", "workers.id"],
            name="fk_worker_credentials_tenant_worker",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_worker_credentials_tenant_id_id"
        ),
        sa.UniqueConstraint("secret_hash"),
    )
    op.create_index(
        "ix_worker_credentials_prefix",
        "worker_credentials",
        ["key_prefix"],
        unique=False,
    )
    op.create_index(
        "uq_worker_credentials_active_worker",
        "worker_credentials",
        ["worker_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    # Downgrade discards credential audit history and connection ownership.
    op.drop_index(
        "uq_worker_credentials_active_worker", table_name="worker_credentials"
    )
    op.drop_index("ix_worker_credentials_prefix", table_name="worker_credentials")
    op.drop_table("worker_credentials")
    op.drop_column("workers", "connection_id")
    op.drop_constraint("ck_workers_status", "workers", type_="check")
    op.alter_column(
        "workers",
        "status",
        existing_type=sa.String(length=30),
        server_default="offline",
        existing_nullable=False,
    )
