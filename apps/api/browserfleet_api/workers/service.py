"""Transactional worker identity, registration, and heartbeat operations."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session, sessionmaker

from browserfleet_api.db.models import Worker, WorkerCredential
from browserfleet_api.workers.protocol import HeartbeatPayload, RegistrationPayload

HEARTBEAT_INTERVAL_SECONDS = 15
WORKER_LEASE_DURATION_SECONDS = 60


class WorkerAuthenticationError(Exception):
    """The presented machine credential cannot establish a connection."""


class WorkerConnectionError(Exception):
    """A valid worker attempted an invalid connection state transition."""


@dataclass(frozen=True, slots=True)
class WorkerPrincipal:
    """Identity derived from a current worker credential, never from its payload."""

    tenant_id: UUID
    worker_id: UUID
    credential_id: UUID


@dataclass(frozen=True, slots=True)
class RegisteredWorker:
    """Server-issued current connection details for the worker client."""

    worker_id: UUID
    connection_id: UUID
    status: str


def authenticate_worker(
    session_factory: sessionmaker[Session], raw_credential: str
) -> WorkerPrincipal:
    """Verify one revocable worker credential and derive its tenant-bound identity."""
    if not raw_credential or len(raw_credential) > 4096:
        raise WorkerAuthenticationError
    now = datetime.now(UTC)
    secret_hash = hashlib.sha256(raw_credential.encode("utf-8")).hexdigest()
    with session_factory.begin() as session:
        row = session.execute(
            select(WorkerCredential, Worker)
            .join(
                Worker,
                (Worker.tenant_id == WorkerCredential.tenant_id)
                & (Worker.id == WorkerCredential.worker_id),
            )
            .where(
                WorkerCredential.key_prefix == raw_credential[:16],
                WorkerCredential.secret_hash == secret_hash,
                WorkerCredential.revoked_at.is_(None),
                or_(
                    WorkerCredential.expires_at.is_(None),
                    WorkerCredential.expires_at > now,
                ),
            )
            .with_for_update()
        ).one_or_none()
        if row is None or row[1].status == "DISABLED":
            raise WorkerAuthenticationError
        credential, worker = row
        credential.last_used_at = now
        return WorkerPrincipal(
            tenant_id=worker.tenant_id,
            worker_id=worker.id,
            credential_id=credential.id,
        )


def register_worker(
    session_factory: sessionmaker[Session],
    principal: WorkerPrincipal,
    payload: RegistrationPayload,
) -> RegisteredWorker:
    """Persist a fresh connection and currently reported capabilities atomically."""
    now = datetime.now(UTC)
    connection_id = uuid4()
    with session_factory.begin() as session:
        worker = _active_credential_worker(session, principal, lock=True, now=now)
        _verify_provisioned_identity(worker, payload)
        worker.capabilities = payload.capabilities
        worker.resource_capacity = payload.resources
        worker.worker_version = payload.worker_version
        worker.protocol_version = "v1"
        if worker.status != "DRAINING":
            worker.status = "ONLINE"
        worker.connection_id = connection_id
        worker.last_heartbeat_at = now
        worker.lease_expires_at = now + timedelta(
            seconds=WORKER_LEASE_DURATION_SECONDS
        )
    return RegisteredWorker(
        worker_id=principal.worker_id,
        connection_id=connection_id,
        status=worker.status,
    )


def record_heartbeat(
    session_factory: sessionmaker[Session],
    principal: WorkerPrincipal,
    connection_id: UUID,
    payload: HeartbeatPayload,
) -> None:
    """Extend the server-issued worker lease only for the current connection."""
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        worker = _active_credential_worker(session, principal, lock=True, now=now)
        if worker.connection_id != connection_id:
            raise WorkerConnectionError
        if payload.free_concurrency > worker.max_concurrency:
            raise WorkerConnectionError
        if worker.status != "DRAINING":
            worker.status = payload.status
        worker.resource_usage = payload.resource_usage
        worker.last_heartbeat_at = now
        worker.lease_expires_at = now + timedelta(
            seconds=WORKER_LEASE_DURATION_SECONDS
        )


def mark_worker_draining(
    session_factory: sessionmaker[Session],
    principal: WorkerPrincipal,
    connection_id: UUID,
) -> None:
    """Persist an explicit drain acknowledgement for the current connection."""
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        worker = _active_credential_worker(session, principal, lock=True, now=now)
        if worker.connection_id != connection_id:
            raise WorkerConnectionError
        worker.status = "DRAINING"


def disconnect_worker(
    session_factory: sessionmaker[Session],
    principal: WorkerPrincipal,
    connection_id: UUID,
) -> None:
    """Take only this connection offline; never clobber a newer reconnect."""
    with session_factory.begin() as session:
        worker = session.scalar(
            select(Worker)
            .where(
                Worker.tenant_id == principal.tenant_id,
                Worker.id == principal.worker_id,
                Worker.connection_id == connection_id,
            )
            .with_for_update()
        )
        if worker is not None and worker.status != "DISABLED":
            worker.status = "OFFLINE"
            worker.connection_id = None


def expire_worker_leases(
    session_factory: sessionmaker[Session], *, now: datetime | None = None
) -> int:
    """Mark workers with elapsed server-side leases offline and detach sockets."""
    expiry_time = now or datetime.now(UTC)
    with session_factory.begin() as session:
        result = cast(
            CursorResult[Any],
            session.execute(
            update(Worker)
            .where(
                Worker.lease_expires_at.is_not(None),
                Worker.lease_expires_at <= expiry_time,
                Worker.status.in_(("REGISTERING", "ONLINE", "BUSY", "DRAINING", "ERROR")),
            )
            .values(status="OFFLINE", connection_id=None)
            ),
        )
        return result.rowcount


def set_worker_enabled(
    session_factory: sessionmaker[Session],
    *,
    tenant_id: UUID,
    worker_id: UUID,
    enabled: bool,
) -> Worker:
    """Enable or disable one tenant-owned worker without trusting client tenancy."""
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        worker = session.scalar(
            select(Worker)
            .where(Worker.tenant_id == tenant_id, Worker.id == worker_id)
            .with_for_update()
        )
        if worker is None:
            raise WorkerConnectionError
        if enabled:
            worker.status = "OFFLINE"
            worker.disabled_at = None
        else:
            worker.status = "DISABLED"
            worker.disabled_at = now
        worker.connection_id = None
        worker.lease_expires_at = None
        return worker


def request_worker_drain(
    session_factory: sessionmaker[Session], *, tenant_id: UUID, worker_id: UUID
) -> Worker:
    """Persist a drain request so it survives WebSocket reconnection."""
    with session_factory.begin() as session:
        worker = session.scalar(
            select(Worker)
            .where(Worker.tenant_id == tenant_id, Worker.id == worker_id)
            .with_for_update()
        )
        if worker is None or worker.status == "DISABLED":
            raise WorkerConnectionError
        worker.status = "DRAINING"
        return worker


def _active_credential_worker(
    session: Session,
    principal: WorkerPrincipal,
    *,
    lock: bool,
    now: datetime,
) -> Worker:
    statement = (
        select(Worker)
        .join(
            WorkerCredential,
            (WorkerCredential.tenant_id == Worker.tenant_id)
            & (WorkerCredential.worker_id == Worker.id),
        )
        .where(
            Worker.tenant_id == principal.tenant_id,
            Worker.id == principal.worker_id,
            WorkerCredential.id == principal.credential_id,
            WorkerCredential.revoked_at.is_(None),
            or_(WorkerCredential.expires_at.is_(None), WorkerCredential.expires_at > now),
        )
    )
    if lock:
        statement = statement.with_for_update()
    worker = session.scalar(statement)
    if worker is None or worker.status == "DISABLED":
        raise WorkerAuthenticationError
    return worker


def _verify_provisioned_identity(worker: Worker, payload: RegistrationPayload) -> None:
    if (
        worker.name != payload.worker_name
        or worker.platform != payload.platform
        or worker.environment != payload.environment
        or worker.hostname != payload.hostname
    ):
        raise WorkerConnectionError
