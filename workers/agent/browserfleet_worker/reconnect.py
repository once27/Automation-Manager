"""Reconnect backoff that remains deterministic under test injection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from random import random


@dataclass(frozen=True, slots=True)
class ExponentialBackoff:
    """Bounded full-jitter reconnect-delay policy."""

    initial_seconds: float = 1.0
    maximum_seconds: float = 30.0
    jitter: Callable[[], float] = random

    def delay(self, failed_attempt: int) -> float:
        """Return a full-jitter delay after a one-based failed attempt."""

        if failed_attempt < 1:
            raise ValueError("failed_attempt must be at least one")
        cap = min(
            self.maximum_seconds, self.initial_seconds * 2 ** (failed_attempt - 1)
        )
        return cap * self.jitter()
