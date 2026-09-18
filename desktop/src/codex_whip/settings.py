from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_MESSAGES = (
    "鞭子响了一下。继续当前任务，马上推进最关键的下一步；不要牺牲正确性，少解释，先产出可验证结果。",
    "检测到一次催工挥动：别停在计划上，继续执行当前任务，完成一个可验证节点后再汇报。",
    "加速干活：沿着当前目标继续推进，优先解决阻塞项，不要重复已经完成的内容。",
)


@dataclass(frozen=True, slots=True)
class BleSettings:
    device_name: str = "CodexWhip"
    scan_timeout_seconds: float = 12.0
    reconnect_seconds: float = 3.0


@dataclass(frozen=True, slots=True)
class EventSettings:
    minimum_interval_seconds: float = 0.4


@dataclass(frozen=True, slots=True)
class CodexSettings:
    target_app: str = "Codex"
    package_marker: str = "OpenAI.Codex_"
    composer_name_hints: tuple[str, ...] = ("message", "ask", "codex", "输入", "消息")
    send_button_name_hints: tuple[str, ...] = ("send", "发送")
    refuse_when_composer_has_text: bool = True


@dataclass(frozen=True, slots=True)
class Settings:
    ble: BleSettings = field(default_factory=BleSettings)
    events: EventSettings = field(default_factory=EventSettings)
    codex: CodexSettings = field(default_factory=CodexSettings)
    messages: tuple[str, ...] = DEFAULT_MESSAGES


def _section(data: dict[str, object], name: str) -> dict[str, object]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a TOML table")
    return value


def load_settings(path: Path | None = None) -> Settings:
    if path is None:
        return Settings()

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    ble = _section(data, "ble")
    events = _section(data, "events")
    codex = _section(data, "codex")
    messages_section = _section(data, "messages")
    raw_messages = messages_section.get("items", DEFAULT_MESSAGES)
    if not isinstance(raw_messages, list | tuple) or not raw_messages:
        raise ValueError("[messages].items must be a non-empty array")
    messages = tuple(str(item).strip() for item in raw_messages if str(item).strip())
    if not messages:
        raise ValueError("[messages].items cannot contain only blank strings")

    settings = Settings(
        ble=BleSettings(
            device_name=str(ble.get("device_name", "CodexWhip")),
            scan_timeout_seconds=float(ble.get("scan_timeout_seconds", 12.0)),
            reconnect_seconds=float(ble.get("reconnect_seconds", 3.0)),
        ),
        events=EventSettings(
            minimum_interval_seconds=float(events.get("minimum_interval_seconds", 0.4))
        ),
        codex=CodexSettings(
            package_marker=str(codex.get("package_marker", "OpenAI.Codex_")),
            composer_name_hints=tuple(
                str(item).lower()
                for item in codex.get(
                    "composer_name_hints", ("message", "ask", "codex", "输入", "消息")
                )
            ),
            send_button_name_hints=tuple(
                str(item).lower()
                for item in codex.get("send_button_name_hints", ("send", "发送"))
            ),
            refuse_when_composer_has_text=bool(
                codex.get("refuse_when_composer_has_text", True)
            ),
        ),
        messages=messages,
    )

    if settings.ble.scan_timeout_seconds <= 0:
        raise ValueError("scan_timeout_seconds must be positive")
    if settings.ble.reconnect_seconds < 0:
        raise ValueError("reconnect_seconds cannot be negative")
    if settings.events.minimum_interval_seconds < 0:
        raise ValueError("minimum_interval_seconds cannot be negative")
    return settings
