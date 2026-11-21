"""Secure outbound Phase 3 worker registration and heartbeat client."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Protocol

from websockets.exceptions import ConnectionClosed

from browserfleet_worker.config import WorkerConfig
from browserfleet_worker.protocol import (
    HeartbeatPayload,
    MessageEnvelope,
    MessageType,
    ProtocolError,
    RegisteredPayload,
    RegistrationPayload,
    WorkerConfigPayload,
    WorkerDrainingPayload,
    WorkerStatus,
    decode_message,
    encode_message,
    make_envelope,
    parse_payload,
)
from browserfleet_worker.reconnect import ExponentialBackoff

logger = logging.getLogger(__name__)


class WorkerTransport(Protocol):
    """Small WebSocket abstraction that permits deterministic client tests."""

    async def send(self, data: str) -> None: ...

    async def recv(self) -> str: ...

    async def close(self) -> None: ...


Connector = Callable[[WorkerConfig], Awaitable[WorkerTransport]]
Sleep = Callable[[float], Awaitable[None]]


class ConnectionRejectedError(RuntimeError):
    """Server rejected this worker or sent an incompatible protocol command."""


class _WebSocketAdapter:
    """Thin adapter that makes a ``websockets.ClientConnection`` satisfy ``WorkerTransport``."""

    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def send(self, data: str) -> None:
        await self._connection.send(data)  # type: ignore[attr-defined]

    async def recv(self) -> str:
        message = await self._connection.recv()  # type: ignore[attr-defined]
        if not isinstance(message, str):
            raise ProtocolError("received non-text WebSocket frame")
        return message

    async def close(self) -> None:
        await self._connection.close()  # type: ignore[attr-defined]


async def connect_websocket(config: WorkerConfig) -> WorkerTransport:
    """Open an authenticated TLS connection without exposing the credential in logs."""

    from websockets.asyncio.client import connect

    connection = await connect(
        config.endpoint,
        additional_headers={
            "Authorization": f"Bearer {config.credential.get_secret_value()}"
        },
        max_size=65_536,
    )
    return _WebSocketAdapter(connection)


class WorkerClient:
    """Reconnectable, non-executing Phase 3 worker control-plane client."""

    def __init__(
        self,
        config: WorkerConfig,
        *,
        connector: Connector = connect_websocket,
        backoff: ExponentialBackoff | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._config = config
        self._connector = connector
        self._backoff = backoff or ExponentialBackoff()
        self._sleep = sleep
        self._stop_requested = False
        self._draining = False
        self._heartbeat_interval_seconds = 15

    @property
    def draining(self) -> bool:
        """Whether the server or local shutdown has placed this worker in drain mode."""

        return self._draining

    @property
    def heartbeat_interval_seconds(self) -> int:
        """Most recently server-authorized heartbeat interval."""

        return self._heartbeat_interval_seconds

    def request_shutdown(self) -> None:
        """End reconnecting after the current connection has closed."""

        self._stop_requested = True

    async def run(self) -> None:
        """Reconnect until shutdown; never restart or execute jobs in Phase 3."""

        failures = 0
        while not self._stop_requested:
            try:
                await self._run_connection()
                failures = 0
            except ConnectionRejectedError:
                raise
            except (TimeoutError, OSError, ConnectionError, ConnectionClosed):
                failures += 1
                delay = self._backoff.delay(failures)
                logger.warning(
                    "worker connection lost; reconnecting in %.2f seconds", delay
                )
                await self._sleep(delay)
            except ProtocolError as error:
                raise ConnectionRejectedError(
                    "server protocol message was invalid"
                ) from error

    async def _run_connection(self) -> None:
        transport = await self._connector(self._config)
        try:
            await self._send_registration(transport)
            await self._receive_registration(transport)
            await self._serve_connection(transport)
        finally:
            with suppress(OSError, ConnectionError):
                await transport.close()

    async def _send_registration(self, transport: WorkerTransport) -> None:
        payload = RegistrationPayload(
            worker_name=self._config.worker_name,
            hostname=self._config.hostname,
            platform=self._config.platform,
            environment=self._config.environment,
            worker_version=self._config.worker_version,
            capabilities=self._config.capabilities,
            resources=self._config.resources,
        )
        await self._send(transport, MessageType.WORKER_REGISTER, payload)

    async def _receive_registration(self, transport: WorkerTransport) -> None:
        envelope = decode_message(await transport.recv())
        if envelope.message_type is not MessageType.WORKER_REGISTERED:
            raise ConnectionRejectedError("worker was not registered by server")
        payload = parse_payload(envelope)
        assert isinstance(payload, RegisteredPayload)
        if payload.worker_id != self._config.worker_id:
            raise ConnectionRejectedError("server registered a different worker identity")
        self._apply_server_config(payload)

    async def _serve_connection(self, transport: WorkerTransport) -> None:
        heartbeat = asyncio.create_task(self._heartbeat_loop(transport))
        try:
            while not self._stop_requested:
                envelope = decode_message(await transport.recv())
                await self._handle_server_message(transport, envelope)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat_loop(self, transport: WorkerTransport) -> None:
        while not self._stop_requested:
            await self._sleep(self._heartbeat_interval_seconds)
            if not self._stop_requested:
                await self._send_heartbeat(transport)

    async def _handle_server_message(
        self, transport: WorkerTransport, envelope: MessageEnvelope
    ) -> None:
        payload = parse_payload(envelope)
        match envelope.message_type:
            case MessageType.WORKER_CONFIG:
                assert isinstance(payload, WorkerConfigPayload)
                self._apply_server_config(payload)
            case MessageType.SERVER_PING:
                await self._send_heartbeat(transport)
            case MessageType.WORKER_DRAIN:
                self._draining = True
                await self._send(
                    transport,
                    MessageType.WORKER_DRAINING,
                    WorkerDrainingPayload(),
                )
            case MessageType.WORKER_SHUTDOWN:
                self._draining = True
                await self._send(
                    transport,
                    MessageType.WORKER_DRAINING,
                    WorkerDrainingPayload(),
                )
                self._stop_requested = True
            case _:
                raise ProtocolError(
                    f"unexpected server message: {envelope.message_type}"
                )

    def _apply_server_config(self, payload: RegisteredPayload) -> None:
        self._heartbeat_interval_seconds = payload.heartbeat_interval_seconds

    async def _send_heartbeat(self, transport: WorkerTransport) -> None:
        await self._send(
            transport,
            MessageType.WORKER_HEARTBEAT,
            HeartbeatPayload(
                status=WorkerStatus.DRAINING if self._draining else WorkerStatus.ONLINE,
                active_lease_ids=[],
                resource_usage={},
                free_concurrency=(
                    0 if self._draining else self._config.capabilities.max_concurrency
                ),
            ),
        )

    async def _send(
        self,
        transport: WorkerTransport,
        message_type: MessageType,
        payload: RegistrationPayload | HeartbeatPayload | WorkerDrainingPayload,
    ) -> None:
        envelope = make_envelope(
            message_type=message_type,
            worker_id=self._config.worker_id,
            payload=payload,
        )
        await transport.send(encode_message(envelope))
