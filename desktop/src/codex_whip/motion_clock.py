"""Frame-aware clocks and small, text-free animation diagnostics."""
from dataclasses import dataclass


ACTIVE_FRAME_MS = 12


class RenderClock:
    """Start on the first painted frame and never skip a whole morph on a stall."""

    MAX_STEP_SECONDS = 1 / 30

    def __init__(self, duration: float) -> None:
        self.duration = duration
        self.elapsed = 0.0
        self._last_frame_at: float | None = None

    @property
    def complete(self) -> bool:
        return self.elapsed >= self.duration

    def sample(self, now: float) -> float:
        if self._last_frame_at is None:
            self._last_frame_at = now
            return 0.0
        delta = max(0.0, min(now - self._last_frame_at, self.MAX_STEP_SECONDS))
        self._last_frame_at = now
        self.elapsed = min(self.duration, self.elapsed + delta)
        return self.elapsed / self.duration if self.duration > 0 else 1.0


@dataclass(frozen=True)
class MotionFrameReport:
    mode: str
    frames: int
    duration_ms: int
    first_frame_wait_ms: int
    interval_p95_ms: int
    interval_max_ms: int
    draw_p95_ms: int


class MotionFrameTrace:
    """Collect one morph's timings without retaining audio, text or pose data."""

    def __init__(self, mode: str, requested_at: float | None = None) -> None:
        self.mode = mode
        self.requested_at = requested_at
        self.started_at: float | None = None
        self._previous_at: float | None = None
        self.intervals: list[float] = []
        self.draws: list[float] = []

    def frame(self, now: float, draw_ms: float) -> None:
        if self.started_at is None:
            self.started_at = now
        if self._previous_at is not None:
            self.intervals.append((now - self._previous_at) * 1000)
        self._previous_at = now
        self.draws.append(draw_ms)

    def report(self) -> MotionFrameReport | None:
        if self.started_at is None or self._previous_at is None:
            return None

        def percentile(values: list[float]) -> int:
            if not values:
                return 0
            ordered = sorted(values)
            return round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))])

        return MotionFrameReport(
            self.mode,
            len(self.draws),
            round((self._previous_at - self.started_at) * 1000),
            round(max(0, (self.started_at - self.requested_at) * 1000))
            if self.requested_at is not None else 0,
            percentile(self.intervals),
            round(max(self.intervals, default=0)),
            percentile(self.draws),
        )
