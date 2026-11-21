"""In-process delivery of Phase 3 worker control commands.

Persisted status is authoritative. This registry only provides prompt delivery to
connections owned by this API process; reconnecting workers reconcile from state.
"""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID

SendCommand = Callable[[str], Awaitable[None]]


class WorkerConnectionRegistry:
    """Track the latest local socket for a worker without storing secrets."""

    def __init__(self) -> None:
        self._connections: dict[UUID, tuple[UUID, SendCommand]] = {}
        self._lock = asyncio.Lock()

    async def register(
        self, *, worker_id: UUID, connection_id: UUID, send: SendCommand
    ) -> None:
        async with self._lock:
            self._connections[worker_id] = (connection_id, send)

    async def remove(self, *, worker_id: UUID, connection_id: UUID) -> None:
        async with self._lock:
            existing = self._connections.get(worker_id)
            if existing is not None and existing[0] == connection_id:
                del self._connections[worker_id]

    async def send(self, worker_id: UUID, message: str) -> bool:
        async with self._lock:
            existing = self._connections.get(worker_id)
        if existing is None:
            return False
        await existing[1](message)
        return True
