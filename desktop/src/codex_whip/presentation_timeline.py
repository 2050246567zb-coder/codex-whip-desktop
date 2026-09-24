"""A responsive visual timeline; device actions are never held by animation."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PresentationFrame:
    key: str
    mode: str
    title: str
    subtitle: str = ""
    deadline: float | None = None


class PresentationTimeline:
    MIN_SECONDS = 1.0
    _EVENT_FRAMES = {
        "recording": PresentationFrame("recording", "recording", "recording"),
        "recognizing": PresentationFrame("recognizing", "recognizing", "recognizing voice"),
    }

    def __init__(self) -> None:
        self.current: PresentationFrame | None = None
        self.entered_at = 0.0
        self.presented_at: float | None = None
        self._queued_recognizing: PresentationFrame | None = None

    def reset(self) -> None:
        self.current = None
        self.entered_at = 0.0
        self.presented_at = None
        self._queued_recognizing = None

    def _enter(self, frame: PresentationFrame, now: float) -> None:
        self.current = frame
        self.entered_at = now
        self.presented_at = None

    def mark_presented(self, key: str, now: float) -> None:
        """The first real shape frame, rather than the event, starts the hold."""
        if self.current is not None and self.current.key == key and self.presented_at is None:
            self.presented_at = now

    def note(self, event: str, now: float) -> None:
        """Keep short real recording/recognition states even between UI polls."""
        frame = self._EVENT_FRAMES[event]
        if event == "recording":
            # Double-tap is feedback, so it preempts idle, loading and an old
            # transcription immediately. No one-second idle queue.
            self._queued_recognizing = None
            self._enter(frame, now)
        elif self.current is not None and self.current.key == "recording":
            self._queued_recognizing = frame
        elif self.current is None or self.current.key != event:
            self._enter(frame, now)

    def resolve(self, desired: PresentationFrame, now: float,
                *, visual_complete: bool = True) -> PresentationFrame:
        if self.current is None:
            self._enter(desired, now)
            return self.current

        if desired.key == self.current.key:
            # Sleep ellipsis and live text updates must not restart the clock.
            self.current = desired
            return self.current

        if desired.key == "recording":
            self._queued_recognizing = None
            self._enter(desired, now)
            return self.current

        if self.current.key in {"recording", "recognizing"}:
            can_advance = (self.presented_at is not None
                           and now - self.presented_at >= self.MIN_SECONDS
                           and visual_complete)
            if not can_advance:
                return self.current

        # Recognition is a real state even when a fast backend has already
        # produced text. Show its transition before the pending result so the
        # user never sees text jump ahead of the infinity animation.
        next_frame = (self._queued_recognizing
                      if self.current.key == "recording"
                      and self._queued_recognizing is not None else desired)
        self._queued_recognizing = None
        self._enter(next_frame, now)
        return self.current
