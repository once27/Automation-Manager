"""PostgreSQL persistence models for Phase 1 domain records."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from browserfleet_api.db.base import Base, CreatedAt, CreatedUpdatedAt, UUIDPrimaryKey

JsonObject = JSONB
StringArray = ARRAY(String())


class Tenant(UUIDPrimaryKey, CreatedUpdatedAt, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="active"
    )
    settings: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class User(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_users_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        CheckConstraint(
            "role IN ('owner', 'admin', 'operator', 'viewer')", name="ck_users_role"
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="active"
    )


Index("uq_users_tenant_email_lower", User.tenant_id, text("lower(email)"), unique=True)


class ApiKey(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "api_keys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_api_keys_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        CheckConstraint(
            "length(key_prefix) BETWEEN 1 AND 16", name="ck_api_keys_prefix_length"
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    secret_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    scopes: Mapped[list[str]] = mapped_column(
        StringArray, nullable=False, default=list, server_default=text("'{}'::text[]")
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_api_keys_tenant_prefix", ApiKey.tenant_id, ApiKey.key_prefix)


class WorkerPool(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "worker_pools"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_worker_pools_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_worker_pools_tenant_name"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        CheckConstraint("max_concurrency > 0", name="ck_worker_pools_max_concurrency"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    labels: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)


class Worker(UUIDPrimaryKey, CreatedUpdatedAt, Base):
    __tablename__ = "workers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workers_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            ["worker_pools.tenant_id", "worker_pools.id"],
            ondelete="RESTRICT",
            name="fk_workers_tenant_pool",
        ),
        CheckConstraint("max_concurrency > 0", name="ck_workers_max_concurrency"),
        CheckConstraint("active_jobs >= 0", name="ck_workers_active_jobs"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    pool_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    hostname: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    environment: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="offline"
    )
    capabilities: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    resource_capacity: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    resource_usage: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    active_jobs: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    worker_version: Mapped[str | None] = mapped_column(String(30))
    protocol_version: Mapped[str | None] = mapped_column(String(20))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_workers_tenant_status", Worker.tenant_id, Worker.status)
Index("ix_workers_pool_status", Worker.tenant_id, Worker.pool_id, Worker.status)
Index("ix_workers_lease_expires", Worker.lease_expires_at)


class WorkflowFamily(UUIDPrimaryKey, CreatedAt, Base):
    """Stable tenant-scoped identity shared by immutable workflow versions."""

    __tablename__ = "workflow_families"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_workflow_families_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "name", name="uq_workflow_families_tenant_name"
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class Workflow(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflows_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "workflow_family_id",
            "version",
            name="uq_workflows_tenant_family_version",
        ),
        UniqueConstraint(
            "tenant_id", "name", "version", name="uq_workflows_tenant_name_version"
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_family_id"],
            ["workflow_families.tenant_id", "workflow_families.id"],
            ondelete="RESTRICT",
            name="fk_workflows_tenant_family",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    workflow_family_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    package_ref: Mapped[str] = mapped_column(Text, nullable=False)
    entrypoint: Mapped[str] = mapped_column(String(255), nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    config_schema: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )


class Job(UUIDPrimaryKey, CreatedUpdatedAt, Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_jobs_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflows.tenant_id", "workflows.id"],
            ondelete="RESTRICT",
            name="fk_jobs_tenant_workflow",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "assigned_worker_id"],
            ["workers.tenant_id", "workers.id"],
            ondelete="RESTRICT",
            name="fk_jobs_tenant_assigned_worker",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
            name="fk_jobs_tenant_created_by",
        ),
        CheckConstraint(
            "priority BETWEEN -32768 AND 32767", name="ck_jobs_priority_range"
        ),
        CheckConstraint("attempt_count >= 0", name="ck_jobs_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_jobs_max_attempts"),
        CheckConstraint("timeout_seconds > 0", name="ck_jobs_timeout_seconds"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    workflow_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="QUEUED"
    )
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="0"
    )
    requirements: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    execution_config: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    artifact_policy: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(JsonObject)
    retry_policy: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1"
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="300"
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_worker_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    current_lease_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="1")
    created_by: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_jobs_tenant_idempotency_key",
    Job.tenant_id,
    Job.idempotency_key,
    unique=True,
    postgresql_where=Job.idempotency_key.is_not(None),
)
Index(
    "ix_jobs_tenant_status_priority_created",
    Job.tenant_id,
    Job.status,
    Job.priority.desc(),
    Job.created_at,
)
Index(
    "ix_jobs_assigned_worker_status", Job.tenant_id, Job.assigned_worker_id, Job.status
)
Index("ix_jobs_tenant_created", Job.tenant_id, Job.created_at)
Index(
    "ix_jobs_nonterminal_created",
    Job.tenant_id,
    Job.created_at,
    postgresql_where=Job.status.not_in(
        ("SUCCEEDED", "FAILED", "CANCELED", "CANCELLED", "TIMED_OUT")
    ),
)


class JobAttempt(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "job_attempts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_job_attempts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "job_id", "attempt_number", name="uq_job_attempts_number"
        ),
        UniqueConstraint("dispatch_id", name="uq_job_attempts_dispatch_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "worker_id"],
            ["workers.tenant_id", "workers.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("attempt_number > 0", name="ck_job_attempts_number"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    dispatch_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    lease_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    execution_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    outcome_unknown: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class JobEvent(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "job_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_job_events_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "attempt_id"],
            ["job_attempts.tenant_id", "job_attempts.id"],
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attempt_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    message_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


Index(
    "uq_job_events_tenant_message_id",
    JobEvent.tenant_id,
    JobEvent.message_id,
    unique=True,
    postgresql_where=JobEvent.message_id.is_not(None),
)
Index(
    "ix_job_events_tenant_job_created",
    JobEvent.tenant_id,
    JobEvent.job_id,
    JobEvent.created_at,
)


class BrowserSession(UUIDPrimaryKey, CreatedUpdatedAt, Base):
    __tablename__ = "browser_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_browser_sessions_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "worker_pool_id"],
            ["worker_pools.tenant_id", "worker_pools.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pinned_worker_id"],
            ["workers.tenant_id", "workers.id"],
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    browser: Mapped[str] = mapped_column(String(30), nullable=False)
    session_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    storage_ref: Mapped[str | None] = mapped_column(Text)
    storage_version: Mapped[str | None] = mapped_column(String(100))
    worker_pool_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    pinned_worker_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, server_default="available"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SessionLease(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "session_leases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_session_leases_tenant_id_id"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["browser_sessions.tenant_id", "browser_sessions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "worker_id"],
            ["workers.tenant_id", "workers.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('active', 'released', 'expired')",
            name="ck_session_leases_status",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    worker_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "uq_session_leases_one_active_per_session",
    SessionLease.tenant_id,
    SessionLease.session_id,
    unique=True,
    postgresql_where=SessionLease.status == "active",
)
Index(
    "ix_session_leases_tenant_status_expiry",
    SessionLease.tenant_id,
    SessionLease.status,
    SessionLease.expires_at,
)


class Artifact(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_artifacts_tenant_id_id"),
        UniqueConstraint("object_key", name="uq_artifacts_object_key"),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "job_id"], ["jobs.tenant_id", "jobs.id"], ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "attempt_id"],
            ["job_attempts.tenant_id", "job_attempts.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_bytes"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    job_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attempt_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    artifact_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum: Mapped[str] = mapped_column(String(128), nullable=False)
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_artifacts_tenant_job_created",
    Artifact.tenant_id,
    Artifact.job_id,
    Artifact.created_at,
)


class AuditLog(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(String(30), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str] = mapped_column(String(50), nullable=False)
    target_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JsonObject,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )


Index("ix_audit_logs_tenant_created", AuditLog.tenant_id, AuditLog.created_at)
Index(
    "ix_audit_logs_target", AuditLog.tenant_id, AuditLog.target_type, AuditLog.target_id
)


class OutboxEvent(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'published', 'failed')",
            name="ck_outbox_events_status",
        ),
    )

    aggregate_type: Mapped[str] = mapped_column(String(50), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JsonObject, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_outbox_events_status_created", OutboxEvent.status, OutboxEvent.created_at)
