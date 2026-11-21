"""Phase 3 worker connection tests against an isolated PostgreSQL schema."""

import hashlib
import json
import os
from collections.abc import AsyncGenerator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from browserfleet_api.db.models import ApiKey, Tenant, Worker, WorkerCredential
from browserfleet_api.db.session import create_session_factory
from browserfleet_api.errors import (
    APIError,
    api_error_handler,
    http_exception_handler,
    validation_error_handler,
)
from browserfleet_api.workers.router import router as workers_router
from browserfleet_api.workers.service import expire_worker_leases
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.websockets import WebSocketDisconnect

API_ROOT = Path(__file__).parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"
WORKER_CREDENTIAL = "bfw_test_worker_credential_0001"
OPERATOR_KEY = "bf_test_worker_operator_0001"


@pytest.fixture
def migrated_engine() -> Iterator[Engine]:
    database_url = os.environ.get("BROWSERFLEET_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("set BROWSERFLEET_TEST_DATABASE_URL to run PostgreSQL integration tests")
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
    test_app.include_router(workers_router)
    with TestClient(test_app) as client:
        yield client


@pytest.fixture
def worker_identity(migrated_engine: Engine) -> tuple[UUID, UUID]:
    with Session(migrated_engine) as session:
        tenant = Tenant(name="Tenant", slug=f"tenant-{uuid4().hex}")
        other_tenant = Tenant(name="Other", slug=f"other-{uuid4().hex}")
        session.add_all([tenant, other_tenant])
        session.flush()
        worker = Worker(
            tenant_id=tenant.id,
            name="linux-01",
            platform="linux",
            environment="docker",
            max_concurrency=2,
        )
        session.add(worker)
        session.flush()
        session.add_all(
            [
                WorkerCredential(
                    tenant_id=tenant.id,
                    worker_id=worker.id,
                    key_prefix=WORKER_CREDENTIAL[:16],
                    secret_hash=hashlib.sha256(
                        WORKER_CREDENTIAL.encode("utf-8")
                    ).hexdigest(),
                ),
                ApiKey(
                    tenant_id=tenant.id,
                    name="operator",
                    key_prefix=OPERATOR_KEY[:16],
                    secret_hash=hashlib.sha256(OPERATOR_KEY.encode("utf-8")).hexdigest(),
                    scopes=["worker:read", "worker:write"],
                ),
            ]
        )
        session.commit()
        return tenant.id, worker.id


def _envelope(worker_id: UUID, message_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "protocol_version": "v1",
            "message_id": str(uuid4()),
            "message_type": message_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "worker_id": str(worker_id),
            "payload": payload,
        }
    )


def _registration(worker_id: UUID) -> str:
    return _envelope(
        worker_id,
        "worker.register",
        {
            "worker_name": "linux-01",
            "platform": "linux",
            "environment": "docker",
            "worker_version": "0.1.0",
            "capabilities": {"browsers": ["chromium"]},
            "resources": {"cpu_cores": 2},
        },
    )


def test_worker_registers_heartbeats_and_receives_drain(
    api_client: TestClient, worker_identity: tuple[UUID, UUID], migrated_engine: Engine
) -> None:
    _, worker_id = worker_identity
    with api_client.websocket_connect(
        "/api/v1/workers/connect", headers={"Authorization": f"Worker {WORKER_CREDENTIAL}"}
    ) as websocket:
        websocket.send_text(_registration(worker_id))
        registered = websocket.receive_json()
        assert registered["message_type"] == "worker.registered"
        assert registered["payload"]["worker_id"] == str(worker_id)

        websocket.send_text(
            _envelope(
                worker_id,
                "worker.heartbeat",
                {"status": "ONLINE", "free_concurrency": 2, "resource_usage": {"cpu": 0.1}},
            )
        )
        drained = api_client.post(
            f"/api/v1/workers/{worker_id}/drain",
            headers={"Authorization": f"Bearer {OPERATOR_KEY}"},
        )
        assert drained.status_code == 200, drained.text
        assert drained.json()["status"] == "DRAINING"
        assert websocket.receive_json()["message_type"] == "worker.drain"

    with Session(migrated_engine) as session:
        worker = session.scalar(select(Worker).where(Worker.id == worker_id))
        assert worker is not None
        assert worker.status == "OFFLINE"
        assert worker.connection_id is None
        assert worker.last_heartbeat_at is not None
        assert worker.resource_usage == {"cpu": 0.1}


def test_revoked_worker_credential_is_rejected(
    api_client: TestClient, worker_identity: tuple[UUID, UUID], migrated_engine: Engine
) -> None:
    _tenant_id, _worker_id = worker_identity
    with Session(migrated_engine) as session:
        credential = session.scalar(select(WorkerCredential))
        assert credential is not None
        credential.revoked_at = datetime.now(UTC)
        session.commit()

    with pytest.raises(WebSocketDisconnect), api_client.websocket_connect(
        "/api/v1/workers/connect",
        headers={"Authorization": f"Worker {WORKER_CREDENTIAL}"},
    ):
        pass


def test_worker_operator_routes_are_tenant_scoped_and_expiry_is_server_owned(
    api_client: TestClient, worker_identity: tuple[UUID, UUID], migrated_engine: Engine
) -> None:
    _tenant_id, worker_id = worker_identity
    missing = api_client.get(
        f"/api/v1/workers/{uuid4()}",
        headers={"Authorization": f"Bearer {OPERATOR_KEY}"},
    )
    assert missing.status_code == 404

    disabled = api_client.post(
        f"/api/v1/workers/{worker_id}/disable",
        headers={"Authorization": f"Bearer {OPERATOR_KEY}"},
    )
    assert disabled.status_code == 204
    enabled = api_client.post(
        f"/api/v1/workers/{worker_id}/enable",
        headers={"Authorization": f"Bearer {OPERATOR_KEY}"},
    )
    assert enabled.status_code == 204

    with Session(migrated_engine) as session:
        worker = session.scalar(select(Worker).where(Worker.id == worker_id))
        assert worker is not None
        worker.status = "ONLINE"
        worker.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
    assert expire_worker_leases(
        create_session_factory(migrated_engine), now=datetime.now(UTC)
    ) == 1
    with Session(migrated_engine) as session:
        worker = session.scalar(select(Worker).where(Worker.id == worker_id))
        assert worker is not None
        assert worker.status == "OFFLINE"
