from __future__ import annotations

import asyncio

from .gate import EventGate
from .messages import PromptSelector
from .models import DeviceMessage, ProtocolMessage, WhipEvent
from .senders.base import PromptSender


class EventProcessor:
    def __init__(
        self, gate: EventGate, prompts: PromptSelector, sender: PromptSender
    ) -> None:
        self._gate = gate
        self._prompts = prompts
        self._sender = sender

    async def handle(self, message: ProtocolMessage) -> None:
        if isinstance(message, DeviceMessage):
            fields = ",".join(message.fields)
            print(f"[DEVICE] {message.kind}{',' if fields else ''}{fields}")
            return

        if not self._gate.accept(message):
            print(f"[EVENT] ignored duplicate/burst WHIP #{message.sequence}")
            return

        prompt = self._prompts.choose(message)
        try:
            result = await asyncio.to_thread(self._sender.send, prompt, message)
        except Exception as exc:  # Keep the BLE reconnect loop alive on UI failure.
            print(f"[CODEX] send failed: {exc}")
            return
        print(f"[CODEX] {result.detail}")


def sample_event() -> WhipEvent:
    return WhipEvent(
        sequence=1,
        peak_gyro_dps=980.0,
        peak_accel_g=3.2,
        duration_ms=120,
        angular_travel_deg=82.5,
        direction_consistency=0.78,
        dominant_axis_ratio=0.66,
        peak_gap_ms=24,
        peak_jerk_gps=186.0,
    )
