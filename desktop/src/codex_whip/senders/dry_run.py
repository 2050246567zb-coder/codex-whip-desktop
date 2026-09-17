from __future__ import annotations

from ..models import WhipEvent
from .base import SendResult


class DryRunSender:
    def send(self, prompt: str, event: WhipEvent) -> SendResult:
        print(
            "[DRY-RUN] "
            f"WHIP #{event.sequence} gyro={event.peak_gyro_dps:.1f}dps "
            f"accel={event.peak_accel_g:.2f}g -> {prompt}"
        )
        return SendResult(sent=False, detail="dry-run: no keyboard input was sent")

