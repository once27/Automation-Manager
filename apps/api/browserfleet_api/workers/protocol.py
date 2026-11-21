"""Versioned, bounded WebSocket message contracts for worker connections."""

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

PROTOCOL_VERSION = "v1"
MAX_MESSAGE_BYTES = 64 * 1024
SUPPORTED_WORKER_MESSAGE_TYPES = frozenset(
    {"worker.register", "worker.heartbeat", "worker.draining"}
)


class ProtocolValidationError(ValueError):
    """Raised when a worker message does not satisfy the stable protocol."""


class MessageEnvelope(BaseModel):
    """Common protocol metadata carried by every worker WebSocket message."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal["v1"]
    message_id: UUID
    message_type: str = Field(min_length=1, max_length=80)
    timestamp: datetime
    worker_id: UUID | None = None
    correlation_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("must include a timezone offset")
        return value.astimezone(UTC)


class RegistrationPayload(BaseModel):
    """Capabilities and immutable identity properties reported on connection."""

    model_config = ConfigDict(extra="forbid")

    worker_name: str = Field(min_length=1, max_length=120)
    hostname: str | None = Field(default=None, min_length=1, max_length=255)
    platform: str = Field(min_length=1, max_length=30)
    environment: str = Field(min_length=1, max_length=40)
    worker_version: str = Field(min_length=1, max_length=30)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, Any] = Field(default_factory=dict)
    active_execution_ids: list[UUID] = Field(
        default_factory=lambda: list[UUID](), max_length=100
    )

    @field_validator(
        "worker_name", "hostname", "platform", "environment", "worker_version"
    )
    @classmethod
    def require_trimmed_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("must not be blank")
        return normalized


class HeartbeatPayload(BaseModel):
    """Current worker health and capacity state reported by an active connection."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["ONLINE", "BUSY", "DRAINING", "ERROR"]
    active_lease_ids: list[UUID] = Field(
        default_factory=lambda: list[UUID](), max_length=100
    )
    resource_usage: dict[str, Any] = Field(default_factory=dict)
    free_concurrency: int = Field(ge=0)


class DrainingPayload(BaseModel):
    """Explicit acknowledgement that the worker will not accept future work."""

    model_config = ConfigDict(extra="forbid")


class WorkerRegisteredPayload(BaseModel):
    """Authoritative Phase 3 connection configuration sent after registration."""

    model_config = ConfigDict(extra="forbid")

    worker_id: UUID
    connection_id: UUID
    heartbeat_interval_seconds: int = Field(gt=0, le=300)
    lease_duration_seconds: int = Field(gt=0, le=3600)
    protocol_version: Literal["v1"]


def parse_worker_message(raw_text: str) -> tuple[MessageEnvelope, BaseModel]:
    """Decode one bounded worker message and its type-specific payload."""
    if len(raw_text.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ProtocolValidationError("message exceeds the maximum allowed size")
    try:
        envelope = MessageEnvelope.model_validate_json(raw_text)
    except ValueError as error:
        raise ProtocolValidationError("message does not match the protocol") from error

    if envelope.message_type not in SUPPORTED_WORKER_MESSAGE_TYPES:
        raise ProtocolValidationError("message type is not supported")
    try:
        if envelope.message_type == "worker.register":
            return envelope, RegistrationPayload.model_validate(envelope.payload)
        if envelope.message_type == "worker.heartbeat":
            return envelope, HeartbeatPayload.model_validate(envelope.payload)
        return envelope, DrainingPayload.model_validate(envelope.payload)
    except ValueError as error:
        raise ProtocolValidationError("message payload does not match the protocol") from error


def server_envelope(
    *,
    message_type: str,
    worker_id: UUID,
    payload: BaseModel | dict[str, Any],
    correlation_id: UUID | None = None,
) -> MessageEnvelope:
    """Build a current-version server-to-worker envelope."""
    serialized_payload = (
        payload.model_dump(mode="json")
        if isinstance(payload, BaseModel)
        else payload
    )
    return MessageEnvelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=uuid4(),
        message_type=message_type,
        timestamp=datetime.now(UTC),
        worker_id=worker_id,
        correlation_id=correlation_id,
        payload=serialized_payload,
    )
