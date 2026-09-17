from __future__ import annotations

import json
import os
import random
import string
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .models import WhipEvent
from .paths import user_data_dir


SendOrder = Literal["sequential", "random"]
ALLOWED_TEMPLATE_FIELDS = frozenset(
    {
        "strength",
        "peak_gyro",
        "peak_accel",
        "angular_travel",
        "direction_consistency",
        "dominant_axis",
        "peak_gap",
        "peak_jerk",
    }
)


@dataclass(frozen=True, slots=True)
class MessageProfile:
    order: SendOrder
    messages: tuple[str, ...]

    def validated(self) -> "MessageProfile":
        if self.order not in {"sequential", "random"}:
            raise ValueError("发送顺序必须是 sequential 或 random")
        cleaned = tuple(message.strip() for message in self.messages if message.strip())
        if not cleaned:
            raise ValueError("至少保留一条发送消息")
        for message in cleaned:
            validate_message_template(message)
        return MessageProfile(self.order, cleaned)


def validate_message_template(template: str) -> None:
    try:
        fields = {
            name.split(".", 1)[0].split("[", 1)[0]
            for _literal, name, _format, _conversion in string.Formatter().parse(template)
            if name
        }
    except ValueError as exc:
        raise ValueError("消息中的大括号不完整") from exc
    unknown = fields - ALLOWED_TEMPLATE_FIELDS
    if unknown:
        raise ValueError(f"不支持的消息变量：{', '.join(sorted(unknown))}")


def reorder_messages(
    messages: tuple[str, ...] | list[str], source: int, target: int
) -> tuple[str, ...]:
    values = list(messages)
    if not 0 <= source < len(values) or not 0 <= target < len(values):
        raise IndexError("消息排序下标越界")
    value = values.pop(source)
    values.insert(target, value)
    return tuple(values)


def default_message_profile_path() -> Path:
    return user_data_dir() / "message-profile.json"


def load_message_profile(path: Path, defaults: tuple[str, ...]) -> MessageProfile:
    fallback = MessageProfile("random", defaults).validated()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            return fallback
        raw_messages = data.get("messages")
        if not isinstance(raw_messages, list):
            return fallback
        return MessageProfile(
            str(data.get("order", "random")),
            tuple(str(item) for item in raw_messages),
        ).validated()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return fallback


def save_message_profile(path: Path, profile: MessageProfile) -> None:
    value = profile.validated()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "order": value.order,
                "messages": list(value.messages),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class MessageProfileStore:
    """Thread-safe live message settings shared by Tk and the BLE worker."""

    def __init__(
        self,
        defaults: tuple[str, ...],
        path: Path | None = None,
        random_source: random.Random | None = None,
    ) -> None:
        self.path = path or default_message_profile_path()
        self._profile = load_message_profile(self.path, defaults)
        self._random = random_source or random.Random()
        self._index = 0
        self._last_random_index: int | None = None
        self._lock = threading.Lock()

    @property
    def profile(self) -> MessageProfile:
        with self._lock:
            return self._profile

    def update(self, profile: MessageProfile) -> None:
        value = profile.validated()
        save_message_profile(self.path, value)
        with self._lock:
            self._profile = value
            self._index = 0
            self._last_random_index = None

    def choose_template(self) -> str:
        with self._lock:
            messages = self._profile.messages
            if self._profile.order == "sequential":
                selected = messages[self._index % len(messages)]
                self._index = (self._index + 1) % len(messages)
                return selected
            selected_index = self._random.randrange(len(messages))
            if len(messages) > 1 and selected_index == self._last_random_index:
                selected_index = (selected_index + 1) % len(messages)
            self._last_random_index = selected_index
            return messages[selected_index]


def strength_label(event: WhipEvent) -> str:
    if event.peak_gyro_dps >= 1500 or event.peak_accel_g >= 6:
        return "猛"
    if event.peak_gyro_dps >= 900 or event.peak_accel_g >= 3:
        return "正常"
    return "轻"


class PromptSelector:
    def __init__(
        self,
        messages: tuple[str, ...] | MessageProfileStore,
        random_source: random.Random | None = None,
    ) -> None:
        if isinstance(messages, MessageProfileStore):
            self._store = messages
        else:
            self._store = MessageProfileStore(messages, Path(os.devnull), random_source)
            self._store._profile = MessageProfile("random", messages).validated()

    def choose(self, event: WhipEvent) -> str:
        template = self._store.choose_template()
        return template.format(
            strength=strength_label(event),
            peak_gyro=round(event.peak_gyro_dps),
            peak_accel=round(event.peak_accel_g, 2),
            angular_travel=round(event.angular_travel_deg or 0, 1),
            direction_consistency=round((event.direction_consistency or 0) * 100),
            dominant_axis=round((event.dominant_axis_ratio or 0) * 100),
            peak_gap=event.peak_gap_ms or 0,
            peak_jerk=round(event.peak_jerk_gps or 0, 1),
        )
