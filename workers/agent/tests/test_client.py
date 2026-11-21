from __future__ import annotations

import asyncio
from collections import deque
from uuid import UUID

from browserfleet_worker.client import WorkerClient, WorkerTransport
from browserfleet_worker.config import WorkerConfig
from browserfleet_worker.protocol import (
    MessageType,
    RegisteredPayload,
    ServerControlPayload,
    decode_message,
    encode_message,
    make_envelope,
)
from browserfleet_worker.reconnect import ExponentialBackoff
from test_config import config_payload


class FakeTransport:
    def __init__(self, incoming: list[str | Exception]) -> None:
        self.incoming = deque(incoming)
        self.sent: list[str] = []
        self.closed = False

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        item = self.incoming.popleft()
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed = True


def server_message(
    worker_id: UUID,
    message_type: MessageType,
    payload: RegisteredPayload | ServerControlPayload,
) -> str:
    return encode_message(
        make_envelope(message_type=message_type, worker_id=worker_id, payload=payload)
    )


def registered(worker_id: UUID) -> str:
    return server_message(
        worker_id,
        MessageType.WORKER_REGISTERED,
        RegisteredPayload(
            worker_id=worker_id,
            connection_id=UUID("00000000-0000-0000-0000-000000000001"),
            heartbeat_interval_seconds=60,
            lease_duration_seconds=240,
        ),
    )


def test_registers_then_drains_and_shuts_down() -> None:
    config = WorkerConfig.model_validate(config_payload())
    transport = FakeTransport(
        [
            registered(config.worker_id),
            server_message(
                config.worker_id,
                MessageType.WORKER_DRAIN,
                ServerControlPayload(reason="maintenance"),
            ),
            server_message(
                config.worker_id,
                MessageType.WORKER_SHUTDOWN,
                ServerControlPayload(reason="stop"),
            ),
        ]
    )

    async def connector(_: WorkerConfig) -> WorkerTransport:
        return transport

    client = WorkerClient(config, connector=connector)
    asyncio.run(client.run())
    sent_types = [decode_message(item).message_type for item in transport.sent]

    assert sent_types == [
        MessageType.WORKER_REGISTER,
        MessageType.WORKER_DRAINING,
        MessageType.WORKER_DRAINING,
    ]
    assert client.draining is True
    assert transport.closed is True


def test_ping_immediately_reports_heartbeat() -> None:
    config = WorkerConfig.model_validate(config_payload())
    transport = FakeTransport(
        [
            registered(config.worker_id),
            server_message(
                config.worker_id, MessageType.SERVER_PING, ServerControlPayload()
            ),
            server_message(
                config.worker_id, MessageType.WORKER_SHUTDOWN, ServerControlPayload()
            ),
        ]
    )

    async def connector(_: WorkerConfig) -> WorkerTransport:
        return transport

    asyncio.run(WorkerClient(config, connector=connector).run())
    sent_types = [decode_message(item).message_type for item in transport.sent]

    assert MessageType.WORKER_HEARTBEAT in sent_types


def test_connection_failure_reconnects_with_jittered_backoff() -> None:
    config = WorkerConfig.model_validate(config_payload())
    first = FakeTransport([OSError("network lost")])
    second = FakeTransport(
        [
            registered(config.worker_id),
            server_message(
                config.worker_id, MessageType.WORKER_SHUTDOWN, ServerControlPayload()
            ),
        ]
    )
    transports = deque([first, second])
    delays: list[float] = []

    async def connector(_: WorkerConfig) -> WorkerTransport:
        return transports.popleft()

    async def sleep(delay: float) -> None:
        delays.append(delay)

    client = WorkerClient(
        config,
        connector=connector,
        backoff=ExponentialBackoff(
            initial_seconds=2, maximum_seconds=2, jitter=lambda: 0.5
        ),
        sleep=sleep,
    )
    asyncio.run(client.run())

    assert delays == [1]
    assert first.closed is True
    assert second.closed is True
