from datetime import UTC, datetime
from uuid import uuid4

import pytest
from browserfleet_worker.protocol import (
    MAX_MESSAGE_BYTES,
    MessageEnvelope,
    MessageType,
    ProtocolError,
    decode_message,
    encode_message,
    make_envelope,
)


def test_round_trip_versioned_envelope() -> None:
    worker_id = uuid4()
    envelope = make_envelope(
        message_type=MessageType.WORKER_HEARTBEAT,
        worker_id=worker_id,
        payload={"status": "ONLINE", "active_execution_ids": [], "resource_usage": {}},
    )

    decoded = decode_message(encode_message(envelope))

    assert decoded.protocol_version == "v1"
    assert decoded.worker_id == worker_id


def test_unsupported_protocol_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="invalid protocol envelope"):
        decode_message(
            MessageEnvelope(
                protocol_version="v1",
                message_id=uuid4(),
                message_type=MessageType.SERVER_PING,
                timestamp=datetime.now(UTC),
                worker_id=uuid4(),
                payload={},
            )
            .model_copy(update={"protocol_version": "v2"})
            .model_dump_json()
        )


def test_oversized_message_is_rejected() -> None:
    with pytest.raises(ProtocolError, match="maximum protocol size"):
        decode_message("x" * (MAX_MESSAGE_BYTES + 1))
