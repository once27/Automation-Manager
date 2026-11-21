"""BrowserFleet worker-agent protocol client."""

from browserfleet_worker.config import WorkerConfig
from browserfleet_worker.protocol import PROTOCOL_VERSION

__all__ = ["PROTOCOL_VERSION", "WorkerConfig"]
