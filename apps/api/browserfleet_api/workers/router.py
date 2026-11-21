"""Worker WebSocket connection and tenant-scoped control endpoints."""

import json
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, field_serializer
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocket, WebSocketDisconnect

from browserfleet_api.auth import APIKeyPrincipal, require_api_key
from browserfleet_api.db.models import Worker
from browserfleet_api.errors import APIError
from browserfleet_api.workers.connections import WorkerConnectionRegistry
from browserfleet_api.workers.protocol import (
    DrainingPayload,
    HeartbeatPayload,
    MessageEnvelope,
    ProtocolValidationError,
    RegistrationPayload,
    WorkerRegisteredPayload,
    parse_worker_message,
    server_envelope,
)
from browserfleet_api.workers.service import (
    HEARTBEAT_INTERVAL_SECONDS,
    WORKER_LEASE_DURATION_SECONDS,
    WorkerAuthenticationError,
    WorkerConnectionError,
    authenticate_worker,
    disconnect_worker,
    mark_worker_draining,
    record_heartbeat,
    register_worker,
    request_worker_drain,
    set_worker_enabled,
)

router = APIRouter(prefix="/api/v1/workers", tags=["workers"])
WorkerReader = Annotated[APIKeyPrincipal, Depends(require_api_key("worker:read"))]
WorkerWriter = Annotated[APIKeyPrincipal, Depends(require_api_key("worker:write"))]


class WorkerResponse(BaseModel):
    """Safe, tenant-scoped worker metadata returned to operators."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    platform: str
    environment: str
    status: str
    capabilities: dict[str, Any]
    resource_capacity: dict[str, Any]
    resource_usage: dict[str, Any]
    max_concurrency: int
    active_jobs: int
    worker_version: str | None
    protocol_version: str | None
    last_heartbeat_at: datetime | None
    lease_expires_at: datetime | None

    @field_serializer("last_heartbeat_at", "lease_expires_at", when_used="json")
    def serialize_timestamp(self, value: datetime | None) -> str | None:
        return (
            None
            if value is None
            else value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        )


def _session(request: Request) -> Generator[Session, None, None]:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        yield session


def _registry(application: Any) -> WorkerConnectionRegistry:
    registry = getattr(application.state, "worker_connections", None)
    if registry is None:
        registry = WorkerConnectionRegistry()
        application.state.worker_connections = registry
    return registry


def _worker_credential(websocket: WebSocket) -> str | None:
    authorization = websocket.headers.get("authorization")
    if authorization is None:
        return None
    scheme, _, credential = authorization.partition(" ")
    if scheme.casefold() != "worker" or not credential:
        return None
    return credential


def _to_json(envelope: MessageEnvelope) -> str:
    return json.dumps(envelope.model_dump(mode="json"), separators=(",", ":"))


async def _send_registered(
    websocket: WebSocket, *, worker_id: UUID, connection_id: UUID
) -> None:
    payload = WorkerRegisteredPayload(
        worker_id=worker_id,
        connection_id=connection_id,
        heartbeat_interval_seconds=HEARTBEAT_INTERVAL_SECONDS,
        lease_duration_seconds=WORKER_LEASE_DURATION_SECONDS,
        protocol_version="v1",
    )
    await websocket.send_text(
        _to_json(
            server_envelope(
                message_type="worker.registered", worker_id=worker_id, payload=payload
            )
        )
    )


async def _send_drain(websocket: WebSocket, worker_id: UUID) -> None:
    await websocket.send_text(
        _to_json(
            server_envelope(message_type="worker.drain", worker_id=worker_id, payload={})
        )
    )


@router.websocket("/connect")
async def connect_worker(websocket: WebSocket) -> None:
    """Authenticate a provisioned worker and process registration and heartbeats."""
    credential = _worker_credential(websocket)
    if credential is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    session_factory = websocket.app.state.session_factory
    try:
        principal = await run_in_threadpool(
            authenticate_worker, session_factory, credential
        )
    except WorkerAuthenticationError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    connection_id: UUID | None = None
    registry = _registry(websocket.app)
    try:
        first_envelope, first_payload = parse_worker_message(
            await websocket.receive_text()
        )
        if (
            first_envelope.message_type != "worker.register"
            or not isinstance(first_payload, RegistrationPayload)
            or (
                first_envelope.worker_id is not None
                and first_envelope.worker_id != principal.worker_id
            )
        ):
            raise ProtocolValidationError("registration is required before heartbeats")
        registered = await run_in_threadpool(
            register_worker, session_factory, principal, first_payload
        )
        connection_id = registered.connection_id
        await _send_registered(
            websocket, worker_id=registered.worker_id, connection_id=connection_id
        )
        await registry.register(
            worker_id=principal.worker_id,
            connection_id=connection_id,
            send=lambda message: websocket.send_text(message),
        )
        if registered.status == "DRAINING":
            await _send_drain(websocket, principal.worker_id)

        while True:
            envelope, payload = parse_worker_message(await websocket.receive_text())
            if envelope.worker_id != principal.worker_id:
                raise ProtocolValidationError("worker identity does not match connection")
            if envelope.message_type == "worker.heartbeat" and isinstance(
                payload, HeartbeatPayload
            ):
                await run_in_threadpool(
                    record_heartbeat,
                    session_factory,
                    principal,
                    connection_id,
                    payload,
                )
            elif envelope.message_type == "worker.draining" and isinstance(
                payload, DrainingPayload
            ):
                await run_in_threadpool(
                    mark_worker_draining,
                    session_factory,
                    principal,
                    connection_id,
                )
            else:
                raise ProtocolValidationError("registration may only occur once")
    except (ProtocolValidationError, WorkerAuthenticationError, WorkerConnectionError):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    except WebSocketDisconnect:
        pass
    finally:
        if connection_id is not None:
            await registry.remove(
                worker_id=principal.worker_id, connection_id=connection_id
            )
            await run_in_threadpool(
                disconnect_worker, session_factory, principal, connection_id
            )


@router.get("/{worker_id}", response_model=WorkerResponse)
def get_worker(
    worker_id: UUID,
    principal: WorkerReader,
    session: Annotated[Session, Depends(_session)],
) -> WorkerResponse:
    """Return one worker only when it belongs to the authenticated tenant."""
    worker = session.get(Worker, worker_id)
    if worker is None or worker.tenant_id != principal.tenant_id:
        raise APIError(
            status_code=404,
            code="WORKER_NOT_FOUND",
            message="The worker was not found.",
        )
    return WorkerResponse.model_validate(worker)


async def _set_enabled(
    *, request: Request, principal: APIKeyPrincipal, worker_id: UUID, enabled: bool
) -> None:
    try:
        await run_in_threadpool(
            set_worker_enabled,
            request.app.state.session_factory,
            tenant_id=principal.tenant_id,
            worker_id=worker_id,
            enabled=enabled,
        )
    except WorkerConnectionError:
        raise APIError(
            status_code=404,
            code="WORKER_NOT_FOUND",
            message="The worker was not found.",
        ) from None


@router.post("/{worker_id}/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_worker(worker_id: UUID, principal: WorkerWriter, request: Request) -> None:
    """Disable a tenant worker and revoke its current connection lease."""
    await _set_enabled(
        request=request, principal=principal, worker_id=worker_id, enabled=False
    )


@router.post("/{worker_id}/enable", status_code=status.HTTP_204_NO_CONTENT)
async def enable_worker(worker_id: UUID, principal: WorkerWriter, request: Request) -> None:
    """Make a disabled worker eligible to reconnect as offline."""
    await _set_enabled(
        request=request, principal=principal, worker_id=worker_id, enabled=True
    )


@router.post("/{worker_id}/drain", response_model=WorkerResponse)
async def drain_worker(worker_id: UUID, principal: WorkerWriter, request: Request) -> WorkerResponse:
    """Persist a no-new-work drain request and deliver it to a local socket."""
    try:
        worker = await run_in_threadpool(
            request_worker_drain,
            request.app.state.session_factory,
            tenant_id=principal.tenant_id,
            worker_id=worker_id,
        )
    except WorkerConnectionError:
        raise APIError(
            status_code=404,
            code="WORKER_NOT_FOUND",
            message="The worker was not found.",
        ) from None
    await _registry(request.app).send(
        worker.id,
        _to_json(
            server_envelope(message_type="worker.drain", worker_id=worker.id, payload={})
        ),
    )
    return WorkerResponse.model_validate(worker)
