"""Versioned WebSocket protocol envelopes and Phase 3 payloads."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from browserfleet_worker.config import WorkerCapabilities, WorkerResources

PROTOCOL_VERSION = "v1"
MAX_MESSAGE_BYTES = 65_536


class ProtocolError(ValueError):
    """Raised for invalid or unsupported peer protocol data."""


class WorkerStatus(StrEnum):
    ONLINE = "ONLINE"
    BUSY = "BUSY"
    DRAINING = "DRAINING"


class MessageType(StrEnum):
    WORKER_REGISTER = "worker.register"
    WORKER_REGISTERED = "worker.registered"
    WORKER_CONFIG = "worker.config"
    WORKER_HEARTBEAT = "worker.heartbeat"
    WORKER_DRAINING = "worker.draining"
    WORKER_DRAIN = "worker.drain"
    WORKER_SHUTDOWN = "worker.shutdown"
    SERVER_PING = "server.ping"


class MessageEnvelope(BaseModel):
    """Common envelope for all worker protocol messages."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: str
    message_id: UUID
    message_type: MessageType
    timestamp: datetime
    worker_id: UUID
    correlation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("protocol_version")
    @classmethod
    def require_supported_protocol(cls, value: str) -> str:
        if value != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version: {value}")
        return value

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")
        return value.astimezone(UTC)


class RegistrationPayload(BaseModel):
    """Initial capability and resource declaration from a provisioned worker."""

    model_config = ConfigDict(extra="forbid")

    worker_name: str = Field(min_length=1, max_length=120)
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    platform: str = Field(min_length=1, max_length=30)
    environment: str = Field(min_length=1, max_length=40)
    worker_version: str = Field(min_length=1, max_length=30)
    capabilities: WorkerCapabilities
    resources: WorkerResources


class RegisteredPayload(BaseModel):
    """Server-confirmed registration parameters."""

    model_config = ConfigDict(extra="forbid")

    worker_id: UUID
    connection_id: UUID
    heartbeat_interval_seconds: int = Field(ge=1, le=3600)
    lease_duration_seconds: int = Field(ge=1, le=86_400)
    protocol_version: str = PROTOCOL_VERSION


class WorkerConfigPayload(RegisteredPayload):
    """Server configuration update applicable to a live connection."""


class HeartbeatPayload(BaseModel):
    """Health observation sent by the connected worker."""

    model_config = ConfigDict(extra="forbid")

    status: WorkerStatus
    active_lease_ids: list[UUID] = Field(default_factory=list[UUID], max_length=100)
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    free_concurrency: int = Field(ge=0)


class WorkerDrainingPayload(BaseModel):
    """Worker acknowledgement that it will not accept future work."""

    model_config = ConfigDict(extra="forbid")


class ServerControlPayload(BaseModel):
    """Optional operator reason supplied with a drain or shutdown command."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


def make_envelope(
    *,
    message_type: MessageType,
    worker_id: UUID,
    payload: BaseModel | dict[str, Any],
    correlation_id: UUID | None = None,
) -> MessageEnvelope:
    """Create a protocol envelope using UTC and a fresh id."""

    body = (
        payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
    )
    return MessageEnvelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=uuid4(),
        message_type=message_type,
        timestamp=datetime.now(UTC),
        worker_id=worker_id,
        correlation_id=correlation_id,
        payload=body,
    )


def encode_message(envelope: MessageEnvelope) -> str:
    """Serialize a validated message after applying the protocol size bound."""

    encoded = envelope.model_dump_json()
    if len(encoded.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message exceeds maximum protocol size")
    return encoded


def decode_message(raw_message: str) -> MessageEnvelope:
    """Parse an incoming peer message and reject unsupported envelopes."""

    if len(raw_message.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolError("message exceeds maximum protocol size")
    try:
        return MessageEnvelope.model_validate_json(raw_message)
    except ValueError as error:
        raise ProtocolError("invalid protocol envelope") from error


def parse_payload(envelope: MessageEnvelope) -> BaseModel:
    """Validate the payload for server commands used in Phase 3."""

    payload_type: type[BaseModel]
    match envelope.message_type:
        case MessageType.WORKER_REGISTERED:
            payload_type = RegisteredPayload
        case MessageType.WORKER_CONFIG:
            payload_type = WorkerConfigPayload
        case MessageType.WORKER_DRAIN | MessageType.WORKER_SHUTDOWN:
            payload_type = ServerControlPayload
        case MessageType.SERVER_PING:
            payload_type = ServerControlPayload
        case _:
            raise ProtocolError(f"unexpected server message: {envelope.message_type}")
    try:
        return payload_type.model_validate(envelope.payload)
    except ValueError as error:
        raise ProtocolError("invalid server message payload") from error
