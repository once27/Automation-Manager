"""PostgreSQL migration and constraint integration tests."""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from browserfleet_api.db.models import (
    ApiKey,
    Artifact,
    BrowserSession,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
    SessionLease,
    Tenant,
    User,
    Worker,
    WorkerPool,
    Workflow,
)
from sqlalchemy import Engine, create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

API_ROOT = Path(__file__).parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    """Apply the initial migration in an isolated temporary PostgreSQL schema."""
    database_url = os.environ.get("BROWSERFLEET_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip(
            "set BROWSERFLEET_TEST_DATABASE_URL to run PostgreSQL integration tests"
        )

    admin_engine = create_engine(database_url)
    schema = f"test_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    config = Config(str(ALEMBIC_INI))
    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    config.attributes["version_table_schema"] = schema
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            command.check(config)
        yield engine
    finally:
        engine.dispose()
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin_engine.dispose()


def test_initial_migration_creates_phase1_tables(migrated_engine: Engine) -> None:
    table_names = set(inspect(migrated_engine).get_table_names())

    assert {
        "tenants",
        "users",
        "api_keys",
        "worker_pools",
        "workers",
        "workflows",
        "jobs",
        "job_attempts",
        "job_events",
        "artifacts",
        "browser_sessions",
        "session_leases",
        "audit_logs",
        "outbox_events",
        "alembic_version",
    } <= table_names
    assert "artifact_bytes" not in table_names
    assert "contents" not in {
        column["name"] for column in inspect(migrated_engine).get_columns("artifacts")
    }


def test_job_idempotency_and_tenant_foreign_keys(migrated_engine: Engine) -> None:
    tenant_a = Tenant(name="Tenant A", slug="tenant-a")
    tenant_b = Tenant(name="Tenant B", slug="tenant-b")

    with Session(migrated_engine) as session:
        session.add_all([tenant_a, tenant_b])
        session.flush()
        workflow_a = Workflow(
            tenant_id=tenant_a.id,
            name="checkout",
            version="1",
            package_ref="registry.example/checkout:1",
            entrypoint="run",
            checksum="sha256:abc",
        )
        workflow_b = Workflow(
            tenant_id=tenant_b.id,
            name="checkout",
            version="1",
            package_ref="registry.example/checkout:1",
            entrypoint="run",
            checksum="sha256:abc",
        )
        session.add_all([workflow_a, workflow_b])
        session.flush()

        first = Job(
            tenant_id=tenant_a.id,
            workflow_id=workflow_a.id,
            workflow_version="1",
            idempotency_key="request-1",
        )
        session.add(first)
        session.flush()

        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                Job(
                    tenant_id=tenant_a.id,
                    workflow_id=workflow_a.id,
                    workflow_version="1",
                    idempotency_key="request-1",
                )
            )
            session.flush()

        # Idempotency keys are tenant-scoped and can be reused by another tenant.
        session.add(
            Job(
                tenant_id=tenant_b.id,
                workflow_id=workflow_b.id,
                workflow_version="1",
                idempotency_key="request-1",
            )
        )
        session.flush()

        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                Job(
                    tenant_id=tenant_a.id,
                    workflow_id=workflow_b.id,
                    workflow_version="1",
                )
            )
            session.flush()


def test_only_one_active_session_lease_per_session(migrated_engine: Engine) -> None:
    tenant = Tenant(name="Tenant", slug="tenant")
    with Session(migrated_engine) as session:
        session.add(tenant)
        session.flush()

        pool = WorkerPool(tenant_id=tenant.id, name="default", max_concurrency=2)
        workflow = Workflow(
            tenant_id=tenant.id,
            name="workflow",
            version="1",
            package_ref="registry.example/workflow:1",
            entrypoint="run",
            checksum="sha256:abc",
        )
        session.add_all([pool, workflow])
        session.flush()
        worker = Worker(
            tenant_id=tenant.id,
            pool_id=pool.id,
            name="worker-1",
            platform="linux",
            environment="docker",
            max_concurrency=2,
        )
        session.add(worker)
        session.flush()
        job = Job(tenant_id=tenant.id, workflow_id=workflow.id, workflow_version="1")
        browser_session = BrowserSession(
            tenant_id=tenant.id,
            name="session",
            browser="chromium",
            session_mode="persistent",
        )
        session.add_all([job, browser_session])
        session.flush()

        lease = SessionLease(
            tenant_id=tenant.id,
            session_id=browser_session.id,
            job_id=job.id,
            worker_id=worker.id,
            status="active",
            expires_at=datetime(2027, 1, 1, tzinfo=UTC),
        )
        session.add(lease)
        session.flush()

        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                SessionLease(
                    tenant_id=tenant.id,
                    session_id=browser_session.id,
                    job_id=job.id,
                    worker_id=worker.id,
                    status="active",
                    expires_at=datetime(2027, 1, 1, tzinfo=UTC),
                )
            )
            session.flush()

        lease.status = "released"
        session.flush()
        session.add(
            SessionLease(
                tenant_id=tenant.id,
                session_id=browser_session.id,
                job_id=job.id,
                worker_id=worker.id,
                status="active",
                expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )
        )
        session.flush()


def test_credential_and_artifact_models_exclude_raw_secret_bytes() -> None:
    assert "secret_hash" in ApiKey.__table__.columns
    assert "secret" not in ApiKey.__table__.columns
    assert "object_key" in Artifact.__table__.columns
    assert not {"contents", "bytes", "data"} & set(Artifact.__table__.columns.keys())
    assert {
        Tenant,
        User,
        WorkerPool,
        Worker,
        Workflow,
        Job,
        JobAttempt,
        JobEvent,
        BrowserSession,
        SessionLease,
        OutboxEvent,
    }
