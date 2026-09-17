from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import WhipEvent


@dataclass(frozen=True, slots=True)
class SendResult:
    sent: bool
    detail: str


class PromptSender(Protocol):
    def send(self, prompt: str, event: WhipEvent) -> SendResult: ...

