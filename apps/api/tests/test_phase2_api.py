"""Phase 2 API behavior tests against an isolated PostgreSQL schema."""

import hashlib
import os
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from browserfleet_api.db.models import (
    ApiKey,
    Job,
    JobAttempt,
    JobEvent,
    OutboxEvent,
    Tenant,
)
from browserfleet_api.db.session import create_session_factory
from browserfleet_api.errors import (
    APIError,
    api_error_handler,
    http_exception_handler,
    validation_error_handler,
)
from browserfleet_api.jobs.router import router as jobs_router
from browserfleet_api.workflows import router as workflows_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

API_ROOT = Path(__file__).parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"
TENANT_A_KEY = "bf_test_tenant_a_key_0001"
TENANT_B_KEY = "bf_test_tenant_b_key_0001"


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    """Apply migrations in a uniquely named schema, then remove that schema."""
    database_url = os.environ.get("BROWSERFLEET_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip(
            "set BROWSERFLEET_TEST_DATABASE_URL to run PostgreSQL integration tests"
        )

    admin_engine = create_engine(database_url)
    schema = f"test_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')

    engine = create_engine(
        database_url, connect_args={"options": f"-csearch_path={schema},public"}
    )
    config = Config(str(ALEMBIC_INI))
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


@pytest.fixture
def api_client(migrated_engine: Engine) -> Iterator[TestClient]:
    """Build the real routers with a session factory bound to the test schema."""

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        application.state.session_factory = create_session_factory(migrated_engine)
        yield

    test_app = FastAPI(
        exception_handlers={
            APIError: api_error_handler,
            RequestValidationError: validation_error_handler,
            StarletteHTTPException: http_exception_handler,
        },
        lifespan=lifespan,
    )
    test_app.include_router(workflows_router)
    test_app.include_router(jobs_router)
    with TestClient(test_app) as client:
        yield client


@pytest.fixture
def test_keys(migrated_engine: Engine) -> dict[str, str]:
    """Create deterministic, hashed keys for two isolated test tenants."""
    records = {
        "tenant_a": (TENANT_A_KEY, ["workflow:read", "workflow:write", "job:read", "job:write"]),
        "tenant_b": (TENANT_B_KEY, ["workflow:read", "workflow:write", "job:read", "job:write"]),
        "workflow_only": ("bf_test_workflow_read_only_key", ["workflow:read"]),
    }
    result: dict[str, str] = {}
    with Session(migrated_engine) as session:
        tenant_a = Tenant(name="Tenant A", slug=f"tenant-a-{uuid4().hex}")
        tenant_b = Tenant(name="Tenant B", slug=f"tenant-b-{uuid4().hex}")
        session.add_all([tenant_a, tenant_b])
        session.flush()
        for name, (raw_key, scopes) in records.items():
            tenant = tenant_b if name == "tenant_b" else tenant_a
            session.add(
                ApiKey(
                    tenant_id=tenant.id,
                    name=name,
                    key_prefix=raw_key[:16],
                    secret_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                    scopes=scopes,
                )
            )
            result[name] = raw_key
        session.commit()
    return result


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _workflow_body(version: str = "1.0.0") -> dict[str, Any]:
    return {
        "name": "checkout",
        "version": version,
        "package_ref": f"registry.example/checkout:{version}",
        "entrypoint": "run",
        "checksum": f"sha256:{version.replace('.', '')}",
        "config_schema": {"type": "object"},
    }


def _create_workflow(client: TestClient, key: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/workflows", headers=_auth(key), json=_workflow_body()
    )
    assert response.status_code == 201, response.text
    return response.json()


def _job_body(version: str = "1.0.0") -> dict[str, Any]:
    return {
        "workflow": {"name": "checkout", "version": version},
        "execution": {"browser": "chromium", "mode": "headless"},
        "requirements": {"platforms": ["linux"]},
        "input": {"record_id": "synthetic-42"},
        "retry_policy": {"max_attempts": 2},
        "artifacts": {"trace": "on_failure"},
    }


def _submit_job(
    client: TestClient,
    key: str,
    body: dict[str, Any] | None = None,
    idempotency_key: str = "job-request-001",
) -> Any:
    """Submit a job through the test client."""
    return client.post(
        "/api/v1/jobs",
        headers={**_auth(key), "Idempotency-Key": idempotency_key},
        json=body or _job_body(),
    )


def test_authentication_scope_and_tenant_isolation(
    api_client: TestClient, test_keys: dict[str, str]
) -> None:
    missing_auth = api_client.get("/api/v1/workflows")
    assert missing_auth.status_code == 401
    assert missing_auth.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

    insufficient_scope = api_client.get(
        "/api/v1/jobs", headers=_auth(test_keys["workflow_only"])
    )
    assert insufficient_scope.status_code == 403
    assert insufficient_scope.json()["error"]["code"] == "INSUFFICIENT_SCOPE"

    workflow = _create_workflow(api_client, test_keys["tenant_a"])
    tenant_b_detail = api_client.get(
        f"/api/v1/workflows/{workflow['id']}",
        headers=_auth(test_keys["tenant_b"]),
    )
    assert tenant_b_detail.status_code == 404
    assert tenant_b_detail.json()["error"]["code"] == "WORKFLOW_NOT_FOUND"


def test_workflow_family_version_creation_and_tenant_scoped_listing(
    api_client: TestClient, test_keys: dict[str, str]
) -> None:
    created = _create_workflow(api_client, test_keys["tenant_a"])
    family_id = created["id"]
    first_version_id = created["versions"][0]["id"]

    added = api_client.post(
        f"/api/v1/workflows/{family_id}/versions",
        headers=_auth(test_keys["tenant_a"]),
        json={key: value for key, value in _workflow_body("2.0.0").items() if key != "name"},
    )
    assert added.status_code == 201, added.text
    assert added.json()["id"] != first_version_id
    assert added.json()["version"] == "2.0.0"

    detail = api_client.get(
        f"/api/v1/workflows/{family_id}", headers=_auth(test_keys["tenant_a"])
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == family_id
    assert [item["version"] for item in detail.json()["versions"]] == [
        "1.0.0",
        "2.0.0",
    ]

    own_list = api_client.get("/api/v1/workflows", headers=_auth(test_keys["tenant_a"]))
    other_list = api_client.get("/api/v1/workflows", headers=_auth(test_keys["tenant_b"]))
    assert [item["id"] for item in own_list.json()["items"]] == [family_id]
    assert other_list.json()["items"] == []


def test_job_submission_idempotency_and_safe_errors(
    api_client: TestClient, test_keys: dict[str, str], migrated_engine: Engine
) -> None:
    _create_workflow(api_client, test_keys["tenant_a"])
    first = _submit_job(api_client, test_keys["tenant_a"])
    assert first.status_code == 202, first.text
    assert first.json()["status"] == "QUEUED"
    assert first.json()["version"] == 1
    assert first.headers["etag"] == '"1"'

    replay = _submit_job(api_client, test_keys["tenant_a"])
    assert replay.status_code == 202
    assert replay.json()["id"] == first.json()["id"]

    different_payload = _job_body()
    different_payload["input"] = {"record_id": "sensitive-input-987"}
    conflict = _submit_job(api_client, test_keys["tenant_a"], different_payload)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert "sensitive-input-987" not in conflict.text

    invalid_version_body = _job_body("missing")
    invalid_version = api_client.post(
        "/api/v1/jobs",
        headers={**_auth(test_keys["tenant_a"]), "Idempotency-Key": "missing-version"},
        json=invalid_version_body,
    )
    assert invalid_version.status_code == 404
    assert invalid_version.json()["error"]["code"] == "WORKFLOW_VERSION_NOT_FOUND"

    foreign_tenant = api_client.get(
        f"/api/v1/jobs/{first.json()['id']}", headers=_auth(test_keys["tenant_b"])
    )
    assert foreign_tenant.status_code == 404
    assert foreign_tenant.json()["error"]["code"] == "JOB_NOT_FOUND"

    own_detail = api_client.get(
        f"/api/v1/jobs/{first.json()['id']}", headers=_auth(test_keys["tenant_a"])
    )
    assert own_detail.status_code == 200
    assert own_detail.json()["input"] == {"record_id": "synthetic-42"}
    own_list = api_client.get("/api/v1/jobs", headers=_auth(test_keys["tenant_a"]))
    assert [item["id"] for item in own_list.json()["items"]] == [first.json()["id"]]
    events = api_client.get(
        f"/api/v1/jobs/{first.json()['id']}/events",
        headers=_auth(test_keys["tenant_a"]),
    )
    assert events.status_code == 200
    assert [item["event_type"] for item in events.json()["items"]] == ["job.queued"]
    assert events.json()["items"][0]["payload"]["to_status"] == "QUEUED"

    with Session(migrated_engine) as session:
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.scalar(select(func.count()).select_from(JobEvent)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
        assert session.scalar(select(func.count()).select_from(JobAttempt)) == 0


def test_cancel_requires_current_etag_and_records_one_transition(
    api_client: TestClient, test_keys: dict[str, str]
) -> None:
    _create_workflow(api_client, test_keys["tenant_a"])
    submitted = _submit_job(api_client, test_keys["tenant_a"])
    job_id = submitted.json()["id"]

    missing = api_client.post(
        f"/api/v1/jobs/{job_id}/cancel", headers=_auth(test_keys["tenant_a"])
    )
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "PRECONDITION_REQUIRED"

    cancelled = api_client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        headers={**_auth(test_keys["tenant_a"]), "If-Match": '"1"'},
    )
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["version"] == 2
    assert cancelled.headers["etag"] == '"2"'

    stale = api_client.post(
        f"/api/v1/jobs/{job_id}/cancel",
        headers={**_auth(test_keys["tenant_a"]), "If-Match": '"1"'},
    )
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "JOB_VERSION_MISMATCH"


def test_manual_retry_preserves_job_attempt_history_and_requires_failed_state(
    api_client: TestClient,
    test_keys: dict[str, str],
    migrated_engine: Engine,
) -> None:
    _create_workflow(api_client, test_keys["tenant_a"])
    submitted = _submit_job(api_client, test_keys["tenant_a"])
    job_id = submitted.json()["id"]

    with Session(migrated_engine) as session:
        job = session.scalar(select(Job).where(Job.id == job_id))
        assert job is not None
        job.status = "FAILED"
        job.version = 4
        job.attempt_count = 1
        job.max_attempts = 2
        attempt = JobAttempt(
            tenant_id=job.tenant_id,
            job_id=job.id,
            attempt_number=1,
            dispatch_id=uuid4(),
            lease_id=uuid4(),
            status="failed",
            error_code="SYNTHETIC_FAILURE",
        )
        session.add(attempt)
        session.commit()
        tenant_id = job.tenant_id
        original_idempotency_key = job.idempotency_key

    retried = api_client.post(
        f"/api/v1/jobs/{job_id}/retry",
        headers={**_auth(test_keys["tenant_a"]), "If-Match": '"4"'},
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["id"] == job_id
    assert retried.json()["status"] == "QUEUED"
    assert retried.json()["version"] == 5
    assert retried.headers["etag"] == '"5"'

    stale_retry = api_client.post(
        f"/api/v1/jobs/{job_id}/retry",
        headers={**_auth(test_keys["tenant_a"]), "If-Match": '"4"'},
    )
    assert stale_retry.status_code == 412

    with Session(migrated_engine) as session:
        job = session.scalar(select(Job).where(Job.id == job_id))
        assert job is not None
        assert job.tenant_id == tenant_id
        assert job.idempotency_key == original_idempotency_key
        assert job.max_attempts == 3
        assert job.attempt_count == 1
        assert session.scalar(
            select(func.count()).select_from(JobAttempt).where(JobAttempt.job_id == job_id)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(JobEvent).where(JobEvent.job_id == job_id)
        ) == 2

    queued = _submit_job(
        api_client,
        test_keys["tenant_a"],
        idempotency_key="job-request-002",
    )
    assert queued.status_code == 202
    not_failed = api_client.post(
        f"/api/v1/jobs/{queued.json()['id']}/retry",
        headers={**_auth(test_keys["tenant_a"]), "If-Match": '"1"'},
    )
    assert not_failed.status_code == 409
    assert not_failed.json()["error"]["code"] == "INVALID_JOB_TRANSITION"


def test_validation_errors_do_not_echo_submitted_values(
    api_client: TestClient, test_keys: dict[str, str]
) -> None:
    sensitive_marker = "SYNTHETIC_SECRET_VALUE_987"
    response = api_client.post(
        "/api/v1/jobs",
        headers=_auth(test_keys["tenant_a"]),
        json={"workflow": sensitive_marker},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert sensitive_marker not in response.text
    assert "Traceback" not in response.text

