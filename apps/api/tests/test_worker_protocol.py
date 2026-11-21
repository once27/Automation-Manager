"""Unit tests for the Phase 3 versioned worker protocol boundary."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from browserfleet_api.workers.protocol import (
    MAX_MESSAGE_BYTES,
    HeartbeatPayload,
    ProtocolValidationError,
    RegistrationPayload,
    parse_worker_message,
)


def _envelope(*, message_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "protocol_version": "v1",
            "message_id": str(uuid4()),
            "message_type": message_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
    )


def test_registration_message_is_versioned_and_typed() -> None:
    worker_id = uuid4()
    envelope, payload = parse_worker_message(
        _envelope(
            message_type="worker.register",
            payload={
                "worker_name": "linux-01",
                "platform": "linux",
                "environment": "docker",
                "worker_version": "0.1.0",
                "capabilities": {"browsers": ["chromium"]},
                "resources": {"cpu_cores": 4},
                "active_execution_ids": [str(worker_id)],
            },
        )
    )

    assert envelope.protocol_version == "v1"
    assert isinstance(payload, RegistrationPayload)
    assert payload.worker_name == "linux-01"
    assert payload.active_execution_ids == [worker_id]


def test_heartbeat_rejects_unknown_fields_and_invalid_status() -> None:
    with pytest.raises(ProtocolValidationError):
        parse_worker_message(
            _envelope(
                message_type="worker.heartbeat",
                payload={
                    "status": "offline",
                    "free_concurrency": 1,
                    "unexpected": "field",
                },
            )
        )


def test_heartbeat_message_uses_canonical_statuses() -> None:
    _, payload = parse_worker_message(
        _envelope(
            message_type="worker.heartbeat",
            payload={"status": "ONLINE", "free_concurrency": 1},
        )
    )

    assert isinstance(payload, HeartbeatPayload)
    assert payload.status == "ONLINE"


@pytest.mark.parametrize(
    "message",
    [
        "not json",
        _envelope(message_type="job.completed", payload={}),
        json.dumps(
            {
                "protocol_version": "v2",
                "message_id": str(uuid4()),
                "message_type": "worker.draining",
                "timestamp": datetime.now(UTC).isoformat(),
                "payload": {},
            }
        ),
    ],
)
def test_invalid_or_unsupported_messages_are_rejected(message: str) -> None:
    with pytest.raises(ProtocolValidationError):
        parse_worker_message(message)


def test_message_size_limit_is_enforced_before_json_parsing() -> None:
    with pytest.raises(ProtocolValidationError):
        parse_worker_message("x" * (MAX_MESSAGE_BYTES + 1))
