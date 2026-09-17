from __future__ import annotations

import time
from collections.abc import Callable

from .models import WhipEvent


class EventGate:
    """Second-layer duplicate and burst protection on the computer."""

    def __init__(
        self,
        minimum_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        duplicate_window_seconds: float = 10.0,
    ) -> None:
        self._minimum_interval = minimum_interval_seconds
        self._duplicate_window = duplicate_window_seconds
        self._clock = clock
        self._last_sequence: int | None = None
        self._last_accepted_at: float | None = None

    def accept(self, event: WhipEvent) -> bool:
        now = self._clock()
        if (
            self._last_sequence == event.sequence
            and self._last_accepted_at is not None
            and now - self._last_accepted_at < self._duplicate_window
        ):
            return False
        if (
            self._last_accepted_at is not None
            and now - self._last_accepted_at < self._minimum_interval
        ):
            return False
        self._last_sequence = event.sequence
        self._last_accepted_at = now
        return True
