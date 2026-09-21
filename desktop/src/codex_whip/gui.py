from __future__ import annotations

import asyncio
import faulthandler
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from tkinter import messagebox
from typing import Any

from . import __version__
from .app import sample_event
from .audio import system_beep
from .ble_client import BleWhipClient
from .calibration import (
    DetectorProfile,
    LearningSample,
    default_profile_path,
    load_profile,
    save_profile,
)
from .effects import CodexWhipEffects
from .gate import EventGate
from .hotkeys import GlobalHotkey, HotkeyRegistrationError, parse_hotkey
from .messages import MessageProfileStore, PromptSelector
from .migration import import_bundled_profile_once
from .models import (
    AudioChunk,
    AudioEnd,
    AudioStart,
    DeviceMessage,
    ProtocolMessage,
    RawMotionBatch,
    WhipEvent,
)
from .motion_v3 import MotionEngine
from .paths import user_data_dir
from .senders import create_live_sender
from .senders.base import SendResult
from .settings import Settings, load_settings
from .settings_window import DetectorSettingsWindow
from .sensor_pose import SensorPoseTracker
from .sensor_bias import load_sensor_bias
from .power_settings import PowerSettings, supports_power_saving
from .mount_profile import default_mounting_path, load_mounting_profile, save_mounting_profile
from .mount_calibration import DirectionCalibration
from .mount_calibration_window import MountCalibrationWindow
from .voice import (
    VoiceModule,
    VoiceSettings,
    VoiceSettingsStore,
    WhisperCppTranscriber,
)
from .visual_settings import VisualSettings, VisualSettingsStore
from .interface_state import audio_display_level
from .virtual_microphone import VirtualMicrophoneBridge, VirtualMicrophoneError


class UiEventBuffer:
    """Thread-safe UI queue that keeps only the newest high-rate frame.

    RAW motion and audio-level notifications can arrive faster than Tk can
    redraw them.  Queueing every intermediate frame makes ``_drain_events``
    chase a queue that never becomes empty and the native window stops
    responding.  State transitions and actions remain lossless; only display
    telemetry is coalesced.
    """

    COALESCED_KINDS = frozenset({"sensor_pose", "ui_audio_level"})

    def __init__(self) -> None:
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._latest: dict[str, Any] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()

    def put(self, item: tuple[str, Any]) -> None:
        kind, payload = item
        if kind not in self.COALESCED_KINDS:
            self._queue.put(item)
            return
        with self._lock:
            self._latest[kind] = payload
            if kind in self._pending:
                return
            self._pending.add(kind)
            # Payload is resolved atomically when the UI consumes this token.
            self._queue.put((kind, None))

    def get_nowait(self) -> tuple[str, Any]:
        kind, payload = self._queue.get_nowait()
        if kind not in self.COALESCED_KINDS:
            return kind, payload
        with self._lock:
            payload = self._latest.pop(kind)
            self._pending.remove(kind)
        return kind, payload

    def empty(self) -> bool:
        return self._queue.empty()


class UiHangWatchdog:
    """Persist Python thread stacks when Tk stops servicing callbacks."""

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float = 3.0,
        repeat_seconds: float = 10.0,
    ) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.repeat_seconds = repeat_seconds
        self._heartbeat_at = time.monotonic()
        self._last_dump_at = -repeat_seconds
        self._context = "startup"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def beat(self, context: str = "idle") -> None:
        with self._lock:
            self._heartbeat_at = time.monotonic()
            self._context = context

    def note(self, context: str) -> None:
        with self._lock:
            self._context = context

    def overdue(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            return (
                now - self._heartbeat_at >= self.timeout_seconds
                and now - self._last_dump_at >= self.repeat_seconds
            )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run,
            name="codex-whip-ui-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.75)

    def _run(self) -> None:
        while not self._stop.wait(0.5):
            now = time.monotonic()
            if not self.overdue(now):
                continue
            with self._lock:
                age = now - self._heartbeat_at
                context = self._context
                self._last_dump_at = now
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
                    stream.write(
                        f"\n=== UI HANG {stamp} version={__version__} "
                        f"heartbeat_age={age:.2f}s context={context} ===\n"
                    )
                    stream.flush()
                    faulthandler.dump_traceback(file=stream, all_threads=True)
                    stream.write("=== END UI HANG ===\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except (OSError, RuntimeError):
                # Diagnostics must never introduce another failure mode.
                pass


def find_config_path() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().with_name("config.toml"))
    else:
        candidates.append(Path.cwd() / "config.toml")

    candidates.append(user_data_dir() / "config.toml")
    return next((path for path in candidates if path.is_file()), None)


class GuiEventProcessor:
    SENSOR_CALIBRATION_MAX_SAMPLE_AGE_SECONDS = 1.5

    def __init__(
        self,
        settings: Settings,
        armed: threading.Event,
        emit: Any,
        motion_engine: MotionEngine | None = None,
        message_store: MessageProfileStore | None = None,
        voice_module: VoiceModule | None = None,
        mounting_path: Path | None = None,
    ) -> None:
        self._gate = EventGate(settings.events.minimum_interval_seconds)
        self._prompts = PromptSelector(message_store or settings.messages)
        self._armed = armed
        self._emit = emit
        self._live_sender: Any | None = None
        self._codex_settings = settings.codex
        self._motion_engine = motion_engine
        self._mounting_path = mounting_path
        self._sensor_pose = SensorPoseTracker(load_mounting_profile(mounting_path) if mounting_path else None)
        self._mount_session: DirectionCalibration | None = None
        self._mount_token = ""
        self._mount_original = self._sensor_pose.mounting
        self._motion_resume_at = 0.0
        self._mount_progress_at = 0.0
        self._last_sensor_batch_at = 0.0
        self._voice = voice_module

    def select_sensor_device(self, identity: str) -> None:
        path = (self._mounting_path.parent if self._mounting_path else user_data_dir()) / "sensor-bias-profiles.json"
        bias = load_sensor_bias(path, identity)
        self._sensor_pose.set_device_bias(bias)
        self._last_sensor_batch_at = 0.0
        if any(bias):
            self._emit("log", "已加载当前手柄的静止零偏补偿："
                       + ", ".join(f"{v:.3f}" for v in bias) + " °/秒")

    def _sender(self) -> Any:
        if self._live_sender is None:
            self._live_sender = create_live_sender(self._codex_settings)
        return self._live_sender

    async def handle(
        self, message: ProtocolMessage, *, source: str = "device"
    ) -> None:
        if isinstance(message, RawMotionBatch):
            self._last_sensor_batch_at = time.monotonic()
            self._emit("sensor_pose", self._sensor_pose.feed_batch(
                message, auto_center=self._mount_session is None))
            if self._mount_session is not None:
                problem = self._mount_session.feed(message)
                if problem:
                    if self._mount_session.stage == "neutral":
                        self._sensor_pose.mounting = None
                        self._sensor_pose.reset()
                    self._mount_state(problem, error=True)
                if time.monotonic() - self._mount_progress_at > 0.1:
                    self._mount_progress_at = time.monotonic()
                    self._emit("mount_progress", (self._mount_token, self._mount_session.angle,
                                                  self._sensor_pose.orientation_reading()))
                return
            if time.monotonic() < self._motion_resume_at:
                return
            voice_triggered = False
            if self._voice is not None:
                voice_triggered, _candidate_active = self._voice.feed_motion(message)
            if self._motion_engine is not None:
                # A lone impact is only a possible first tap and can also be
                # part of a real whip.  Let V3 inspect it; suppress only a
                # completed double tap (or an active calibration session).
                event = (
                    None
                    if voice_triggered or (self._voice is not None and self._voice.calibration_active)
                    else self._motion_engine.feed_batch(message)
                )
                if event is not None:
                    score = self._motion_engine.last_score
                    if score is not None:
                        negative = "--" if score[1] is None else f"{score[1]:.3f}"
                        self._emit(
                            "log",
                            f"V3 模板通过：正例距离 {score[0]:.3f}，反例距离 {negative}",
                        )
                    await self.handle(event, source="motion_v3")
            return
        if isinstance(message, AudioStart | AudioChunk | AudioEnd):
            if self._voice is not None:
                await self._voice.handle_audio(message)
            return
        if isinstance(message, LearningSample):
            self._emit("learning_sample", message)
            return
        if isinstance(message, DeviceMessage):
            if message.kind == 'POWER' and len(message.fields) >= 2:
                state = message.fields[1]
                if state in ('SLEEP', 'ACTIVE') and state != getattr(self, '_power_state', None):
                    self._power_state = state
                    self._sensor_pose.reset()
                    self._last_sensor_batch_at = 0.0
                    if self._motion_engine is not None:
                        self._motion_engine.reset_stream()
                    if self._voice is not None:
                        self._voice.detector.reset()
                    self._motion_resume_at = time.monotonic() + 0.5
            self._emit("device", message)
            return

        if self._mount_session is not None or time.monotonic() < self._motion_resume_at:
            return

        if self._voice is not None and self._voice.calibration_active:
            return  # Includes device, mouse and simulated strikes during testing.

        if (
            source == "device"
            and self._voice is not None
            and self._voice.store.settings.enabled
            and self._voice.blocks_device_whip
        ):
            self._emit("log", f"已完成双敲，忽略同一动作尾部的挥鞭事件 #{message.sequence}")
            return

        if (
            source == "device"
            and self._motion_engine is not None
            and self._motion_engine.trained
        ):
            self._emit("log", f"已由 V3 接管，忽略开发板旧事件 #{message.sequence}")
            return

        if not self._gate.accept(message):
            self._emit("log", f"忽略过近或重复的挥动 #{message.sequence}")
            return

        voice_prompt = self._voice.pending_text if self._voice is not None else None
        native_draft = bool(self._voice is not None and self._voice.native_draft_pending)
        prompt = voice_prompt or ("Codex 原生听写" if native_draft else self._prompts.choose(message))
        self._emit(
            "whip",
            {
                "sequence": message.sequence,
                "gyro": message.peak_gyro_dps,
                "accel": message.peak_accel_g,
                "duration": message.duration_ms,
                "angular_travel": message.angular_travel_deg,
                "direction_consistency": message.direction_consistency,
                "dominant_axis": message.dominant_axis_ratio,
                "peak_gap": message.peak_gap_ms,
                "peak_jerk": message.peak_jerk_gps,
                "prompt": prompt,
                "source": source,
                "voice_prompt": voice_prompt is not None or native_draft,
                "native_dictation": native_draft,
            },
        )

        if not self._armed.is_set():
            result = SendResult(
                sent=False,
                detail="安全监听：已收到挥动，但没有向 Codex 输入文字",
            )
        else:
            try:
                sender = self._sender()
                operation = sender.submit_existing if native_draft else sender.send
                result = await asyncio.to_thread(operation, message) if native_draft else await asyncio.to_thread(operation, prompt, message)
            except Exception as exc:
                self._emit("send_error", str(exc))
                return
        if result.sent and voice_prompt is not None and self._voice is not None:
            self._voice.mark_sent(prompt)
        if result.sent and native_draft and self._voice is not None:
            self._voice.clear_native_draft()
        self._emit("send_result", result)

    def calibrate_sensor_neutral(self) -> None:
        """Calibrate the latest hand-held orientation on the worker thread."""
        if self._mount_session is not None:
            self._emit("sensor_calibration", (False, "请先完成或取消手柄方向向导。"))
            return
        age = time.monotonic() - self._last_sensor_batch_at
        if self._last_sensor_batch_at <= 0.0 or age > self.SENSOR_CALIBRATION_MAX_SAMPLE_AGE_SECONDS:
            self._emit(
                "sensor_calibration",
                (False, "没有收到近期六轴数据；请先连接握柄并保持原始数据传输。"),
            )
            return
        try:
            pose = self._sensor_pose.calibrate_neutral(allow_motion=True)
        except ValueError as exc:
            self._emit("sensor_calibration", (False, str(exc)))
            return
        if pose is None:
            self._emit("sensor_calibration", (False, self._sensor_pose.orientation_reading().detail))
            return
        self._emit("sensor_pose", pose)
        self._emit(
            "sensor_calibration",
            (True, "手持零点已校准；当前姿态对应 Codex 窗口中心。"),
        )

    def _mount_state(self, detail: str = "", *, error: bool = False) -> None:
        session = self._mount_session
        if session is not None:
            if error:
                self._emit("log", f"方向校准 [{session.stage}]：{detail}")
            self._emit("mount_state", {"token": self._mount_token, "stage": session.stage,
                                      "detail": detail, "error": error, "centered": session.centered})

    def _write_mount_diagnostic(self, action: str, detail: str, *, error: bool = False) -> None:
        if self._mount_session is None or self._mounting_path is None:
            return
        # Retain only the most recent failure and capture, independently from
        # all learned profiles. File I/O happens only on a manual button press.
        name = "calibration-last-error.json" if error else "calibration-last-capture.json"
        path = self._mounting_path.with_name(name)
        temporary = path.with_suffix(".tmp")
        try:
            payload = self._mount_session.diagnostic_snapshot()
            payload.update(version=__version__, action=action, detail=detail)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
            os.replace(temporary, path)
        except (OSError, ValueError) as exc:
            self._emit("log", f"校准诊断记录失败（不影响校准）：{exc}")

    def mount_command(self, action: str, token: str) -> None:
        if action == "open":
            if self._mount_session is not None:
                return
            self._armed.clear()
            self._mount_token = token
            self._mount_original = self._sensor_pose.mounting
            self._sensor_pose.mounting = None
            self._sensor_pose.reset()
            self._mount_session = DirectionCalibration(self._sensor_pose)
            if self._motion_engine is not None:
                self._motion_engine.set_paused(True)
            if self._voice is not None:
                self._voice.detector.reset()
            self._mount_state("已暂停动作触发。请自然握持，手柄大致水平朝向屏幕。")
            return
        if self._mount_session is None or token != self._mount_token:
            return
        session = self._mount_session
        if action == "cancel":
            self._sensor_pose.mounting = self._mount_original
            self._sensor_pose.reset()
            self._end_mount_session()
            self._emit("mount_closed", {"token": token, "saved": False})
            return
        if action in ("restart", "disconnect"):
            self._sensor_pose.mounting = None
            self._sensor_pose.reset()
            if action == "disconnect":
                session.connection_lost()
                self._last_sensor_batch_at = 0.0
            else:
                session.invalidate()
            self._mount_state("连接已中断；已完成步骤保留。重连后继续本步，预览页需重新归中。" if action == "disconnect"
                              else "已重新开始；旧档案会保留到确认保存。", error=action == "disconnect")
            return
        try:
            if self._last_sensor_batch_at <= 0 or time.monotonic() - self._last_sensor_batch_at > 1.5:
                raise ValueError("尚未收到近期六轴数据；请连接手柄，保持监听后再试。")
            if action == "neutral":
                session.record_neutral()
            elif action == "begin":
                session.begin()
            elif action == "finish":
                session.finish()
                self._write_mount_diagnostic(action, session.feedback)
            elif action == "retry":
                session.retry()
            elif action == "center":
                if session.stage != "review" or session.candidate is None:
                    raise ValueError("请先录入两个方向的动作。")
                previous = self._sensor_pose.mounting
                self._sensor_pose.mounting = session.candidate
                try:
                    pose = self._sensor_pose.calibrate_neutral(allow_motion=True)
                    if pose is None:
                        raise ValueError(self._sensor_pose.orientation_reading().detail)
                except ValueError:
                    self._sensor_pose.mounting = previous
                    raise
                session.centered = True
                self._emit("sensor_pose", pose)
            elif action == "save":
                if session.candidate is None or not session.centered or self._mounting_path is None:
                    raise ValueError("请先归中并试方向，确认正确后再保存。")
                save_mounting_profile(session.candidate, self._mounting_path)
                self._sensor_pose.mounting = session.candidate
                self._end_mount_session()
                self._emit("mount_closed", {"token": token, "saved": True})
                return
            else:
                return
            self._mount_state("已归中，请试转手柄，确认圆点与动作方向一致。" if action == "center" else
                              session.feedback or "起点已记录。按上方提示操作；采集没有时间限制。")
        except (ValueError, OSError) as exc:
            self._write_mount_diagnostic(action, str(exc), error=True)
            self._mount_state(str(exc), error=True)

    def _end_mount_session(self) -> None:
        self._mount_session = None
        self._motion_resume_at = time.monotonic() + 1.0
        if self._motion_engine is not None:
            self._motion_engine.set_paused(False)
        if self._voice is not None:
            self._voice.detector.reset()


class CodexWhipWindow:
    BG = "#0B0E13"
    CARD = "#151A22"
    CARD_ALT = "#10151C"
    TEXT = "#F3F5F7"
    MUTED = "#8E9AAA"
    LINE = "#28313E"
    ACCENT = "#F1B84B"
    GREEN = "#49C98B"
    RED = "#FF6B6B"
    BLUE = "#64A8FF"

    def __init__(self, root: tk.Tk, settings: Settings, config_path: Path | None) -> None:
        self.root = root
        self.settings = settings
        try:
            target = json.loads((user_data_dir() / 'target-app.json').read_text(encoding='utf-8'))['target']
            if target in ('Codex', 'Claude'):
                self.settings = replace(settings, codex=replace(settings.codex, target_app=target))
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.config_path = config_path
        self.events = UiEventBuffer()
        self.armed = threading.Event()
        self.worker_thread: threading.Thread | None = None
        self.worker_loop: asyncio.AbstractEventLoop | None = None
        self.worker_stop: asyncio.Event | None = None
        self.processor: GuiEventProcessor | None = None
        self.command_queue: asyncio.Queue[str] | None = None
        self.closing = False
        self.ble_connected = False
        self.firmware_supports_settings = False
        self.firmware_supports_raw = False
        self.firmware_supports_voice = False
        self.firmware_version = ""
        self.power_store = PowerSettings(user_data_dir() / 'power-settings.json')
        self.power_status = '连接手柄后同步'
        self.detector_profile_path = default_profile_path()
        self.detector_profile = load_profile(self.detector_profile_path)
        self.motion_engine = MotionEngine()
        self.message_store = MessageProfileStore(settings.messages)
        self.visual_store = VisualSettingsStore()
        self.voice_store = VoiceSettingsStore()
        self.virtual_microphone = VirtualMicrophoneBridge()
        self._dictation_session: Any | None = None
        self._dictation_watchdog: str | None = None
        from .cloud_speech import SpeechRouter
        self.voice_transcriber = SpeechRouter(self.voice_store, WhisperCppTranscriber())
        self.voice_module = VoiceModule(
            self.voice_store, lambda kind, payload: self.emit(kind, payload),
            self.voice_transcriber,
            virtual_microphone=self.virtual_microphone,
        )
        self._voice_model_preparing = False
        self.settings_window: DetectorSettingsWindow | None = None
        self.mounting_path = default_mounting_path()
        self.mount_window: MountCalibrationWindow | None = None
        # First use is presented by Interface; never surprise-open a modal on data.
        self._mount_prompted = True
        self._arm_generation = 0
        self._profile_ack_count = 0
        self._effect_target_handle: int | None = None
        self._effect_refresh_after: str | None = None
        self._effect_refresh_running = False
        self._manual_sequence = 1_000_000
        self._scare_hotkey: GlobalHotkey | None = None
        self._hang_watchdog = UiHangWatchdog(user_data_dir() / "hang-diagnostics.log")
        self._hang_heartbeat_after: str | None = None

        self.arm_value = tk.BooleanVar(value=False)
        self.ble_value = tk.StringVar(value="尚未启动")
        self.codex_value = tk.StringVar(value="正在检查")
        self.mode_value = tk.StringVar(value="安全监听")
        self.last_event_value = tk.StringVar(value="等待第一次挥动")
        self.voice_status_value = tk.StringVar(
            value="已关闭（可在设置中启用）"
            if not self.voice_store.settings.enabled
            else "等待双敲手柄"
        )

        self._build_window()
        if load_mounting_profile(self.mounting_path) is None:
            self.sensor_calibrate_button.configure(text="首次方向校准")
        self.effects = CodexWhipEffects(
            self.root,
            lambda line: self.emit("log", line),
            manual_whip=self._handle_mouse_whip,
            damage_interval=self.visual_store.settings.strikes_per_wound,
        )
        self.effects.set_scare_timing(
            self.visual_store.settings.scare_blackout_ms,
            self.visual_store.settings.scare_eyes_ms,
        )
        self.effects.set_feedback(wounds_enabled=self.visual_store.settings.wounds_enabled,
                                  sound_enabled=self.visual_store.settings.sound_enabled)
        self._replace_scare_hotkey(self.visual_store.settings, log=True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._hang_watchdog.start()
        self._hang_heartbeat()
        self._drain_after = self.root.after(16, self._drain_events)
        self._listen_after = self.root.after(250, self.start_listening)
        self._check_after = self.root.after(500, self.check_codex)
        self._schedule_effect_target_refresh(1200)

    def _build_window(self) -> None:
        from .interface import Interface
        self.ui = Interface(self)


    def emit(self, kind: str, payload: Any) -> None:
        self.events.put((kind, payload))

    def _hang_heartbeat(self) -> None:
        self._hang_heartbeat_after = None
        if self.closing:
            return
        self._hang_watchdog.beat("tk-event-loop")
        self._hang_heartbeat_after = self.root.after(250, self._hang_heartbeat)

    def _worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        stop = asyncio.Event()
        command_queue: asyncio.Queue[str] = asyncio.Queue()
        processor = GuiEventProcessor(
            self.settings,
            self.armed,
            self.emit,
            self.motion_engine,
            self.message_store,
            self.voice_module,
            mounting_path=self.mounting_path,
        )
        self.worker_loop = loop
        self.worker_stop = stop
        self.processor = processor
        self.command_queue = command_queue
        self.emit("worker_started", None)
        client = BleWhipClient(
            self.settings.ble,
            log_handler=lambda line: self.emit("log", line),
            state_handler=lambda state: self.emit("ble", state),
            command_queue=command_queue,
            device_handler=processor.select_sensor_device,
        )
        try:
            async def handle_with_meter(message: ProtocolMessage) -> None:
                # Display-only observer. The original processor sees every packet
                # unchanged, and owns all recognition/recording decisions.
                if isinstance(message, AudioChunk):
                    self.emit("ui_audio_level", audio_display_level(message))
                elif (
                    isinstance(message, DeviceMessage)
                    and message.kind == "PONG"
                    and message.fields
                    and self._version_at_least(message.fields[0], (0, 3, 1))
                ):
                    # Synchronize detection thresholds on the BLE worker as
                    # soon as PONG arrives.  Previously this waited for Tk's UI
                    # queue, so a busy animation/raw stream could leave stale
                    # (often much higher) firmware thresholds active.
                    for command in self.detector_profile.commands():
                        command_queue.put_nowait(command)
                    self.emit("profile_sync_started", len(self.detector_profile.commands()))
                await processor.handle(message)

            loop.run_until_complete(client.run(handle_with_meter, stop))
        except Exception as exc:
            self.emit("log", f"监听线程异常：{exc}")
        finally:
            if processor._mount_session is not None:
                processor.mount_command("cancel", processor._mount_token)
            self.worker_loop = None
            self.worker_stop = None
            self.processor = None
            self.command_queue = None
            loop.close()
            self.emit("worker_stopped", None)

    def start_listening(self) -> None:
        if self.worker_thread is not None and self.worker_thread.is_alive():
            return
        self.ble_value.set("正在启动")
        self.listen_button.configure(text="停止监听", command=self.stop_listening)
        self.worker_thread = threading.Thread(
            target=self._worker_main,
            name="codex-whip-ble",
            daemon=True,
        )
        self.worker_thread.start()

    def stop_listening(self) -> None:
        self.armed.clear()
        self.arm_value.set(False)
        self.mode_value.set("安全监听")
        if self.worker_loop is not None and self.worker_stop is not None:
            self.worker_loop.call_soon_threadsafe(self.worker_stop.set)
        self.ble_value.set("正在停止")
        self.listen_button.configure(state="disabled")

    def send_device_command(self, command: str) -> bool:
        if (
            not self.ble_connected
            or self.worker_loop is None
            or self.command_queue is None
        ):
            return False
        queue_ref = self.command_queue
        self.worker_loop.call_soon_threadsafe(queue_ref.put_nowait, command)
        return True

    def send_settings_command(self, command: str) -> bool:
        if not self.firmware_supports_settings:
            return False
        return self.send_device_command(command)

    def _set_power_status(self, value: str) -> None:
        self.power_status = value
        window = self.settings_window
        if window is not None and window.window.winfo_exists():
            window.power_status.set(value)

    def apply_power_settings(self, enabled: bool) -> bool:
        try:
            self.power_store.save(enabled)
        except OSError as exc:
            messagebox.showerror('未能保存省电设置', str(exc))
            return False
        if not self.ble_connected:
            self._set_power_status('已保存，连接后同步')
        elif not supports_power_saving(self.firmware_version):
            self._set_power_status('需要旧 XIAO 固件 0.6.1；当前尚未生效')
        elif self.send_device_command(self.power_store.command()):
            self._set_power_status('等待手柄确认…')
        else:
            self._set_power_status('已保存，等待重新连接')
        return True

    def calibrate_sensor_neutral(self) -> None:
        if load_mounting_profile(self.mounting_path) is None:
            self.open_mount_calibration()
            return
        loop = self.worker_loop
        processor = self.processor
        if loop is None or processor is None:
            messagebox.showinfo("尚未监听", "请先启动监听并连接握柄，再校准手持零点。")
            return
        self.sensor_calibrate_button.configure(state="disabled", text="校准中…")
        loop.call_soon_threadsafe(processor.calibrate_sensor_neutral)

    def open_mount_calibration(self) -> None:
        if self.mount_window is not None:
            self.mount_window.window.lift()
            return
        if self.worker_loop is None or self.processor is None:
            messagebox.showinfo("尚未监听", "请先启动监听并连接手柄，再做方向校准。")
            return
        if self.voice_module.calibration_active or (self.settings_window is not None
                and self.settings_window.window.winfo_exists()
                and self.settings_window._stage in {"positive", "negative"}):
            messagebox.showinfo("请先结束动作学习", "请先完成或关闭现有挥鞭 / 双敲学习，再做手柄方向校准。")
            return
        self._mount_prompted = True
        self._arm_generation += 1  # Invalidate a pending pre-calibration arm check.
        self.armed.clear()
        self.arm_value.set(False)
        self.mode_value.set("方向校准 · 暂停发送")
        self.mount_window = MountCalibrationWindow(self.root, self._send_mount_command,
                                                   self._mount_dismissed)
        self.mount_window.window.grab_set()
        self._send_mount_command("open", self.mount_window.token)

    def _send_mount_command(self, action: str, token: str) -> None:
        if self.worker_loop is not None and self.processor is not None:
            self.worker_loop.call_soon_threadsafe(self.processor.mount_command, action, token)
        elif self.mount_window is not None:
            self.mount_window.update_state({"stage": "neutral", "error": True,
                                            "detail": "监听已停止，请关闭向导并重新启动监听。"})

    def _mount_dismissed(self) -> None:
        self.mount_window = None
        self.mode_value.set("安全监听")
        self.armed.clear()
        self.arm_value.set(False)

    @staticmethod
    def _version_at_least(value: str, required: tuple[int, int, int]) -> bool:
        try:
            parts = tuple(int(part) for part in value.split(".")[:3])
        except ValueError:
            return False
        normalized = parts + (0,) * (3 - len(parts))
        return normalized >= required

    def _queue_detector_profile(self) -> bool:
        if not self.firmware_supports_settings:
            return False
        queued = True
        self._profile_ack_count = 0
        for command in self.detector_profile.commands():
            queued = self.send_device_command(command) and queued
        return queued

    def apply_detector_profile(self, profile: DetectorProfile) -> bool:
        try:
            save_profile(profile, self.detector_profile_path)
        except OSError as exc:
            messagebox.showerror("无法保存设置", str(exc))
            return False
        self.detector_profile = profile
        queued = self._queue_detector_profile()
        self.emit(
            "log",
            "检测阈值已保存并等待设备确认"
            if queued
            else "检测阈值已保存；设备支持并连接后会自动下发",
        )
        return queued

    def apply_voice_settings(self, settings: VoiceSettings) -> bool:
        previous = self.voice_store.settings
        try:
            self.voice_store.update(settings)
            self.voice_module.update_settings()
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法保存语音设置", str(exc))
            return False
        if previous.input_mode != settings.input_mode:
            self._stop_native_dictation(abort=True)
            self.voice_module.clear_pending()
            self.voice_module.clear_native_draft()
        if self.ble_connected and self.firmware_supports_voice:
            self.send_device_command("VOICE,1" if settings.enabled else "VOICE,0")
        if settings.enabled and settings.input_mode == "transcription":
            self.voice_status_value.set("正在准备语音识别")
            self.prepare_voice_model()
        elif settings.enabled:
            try:
                device = self.virtual_microphone.detect()
                self.voice_status_value.set(f"原生听写已就绪 · {device.name}")
            except VirtualMicrophoneError as exc:
                self.voice_status_value.set(str(exc))
        else:
            self.voice_status_value.set("已关闭（可在设置中启用）")
        self.emit("log", "语音双敲模块已开启" if settings.enabled else "语音双敲模块已关闭")
        return True

    def apply_visual_settings(self, settings: VisualSettings) -> bool:
        try:
            settings = replace(settings, scare_enabled=False).validated()
            self.visual_store.update(settings)
            self.effects.set_damage_interval(settings.strikes_per_wound)
            self.effects.set_feedback(wounds_enabled=settings.wounds_enabled,
                                      sound_enabled=settings.sound_enabled)
        except (OSError, ValueError) as exc:
            messagebox.showerror("无法保存视觉设置", str(exc))
            return False
        self.emit("log", f"视觉设置已保存：每 {settings.strikes_per_wound} 次显示伤口")
        return True

    def _replace_scare_hotkey(self, settings: VisualSettings, *, log: bool) -> bool:
        # Retired feature: never register a shortcut, even with legacy settings.
        previous = getattr(self, "_scare_hotkey", None)
        if previous is not None:
            previous.stop()
        self._scare_hotkey = None
        return True

    def prepare_voice_model(self) -> None:
        if self.voice_transcriber.ready:
            self.emit("voice_model_ready", None)
            return
        if self._voice_model_preparing:
            return
        self._voice_model_preparing = True

        def prepare() -> None:
            try:
                self.voice_transcriber.prepare(
                    lambda done, total: self.emit(
                        "voice_model_progress", (done, total)
                    )
                )
                self.emit("voice_model_ready", None)
            except Exception as exc:
                self.emit("voice_model_error", str(exc))

        threading.Thread(
            target=prepare, name="codex-whip-voice-model", daemon=True
        ).start()

    def _stop_native_dictation(self, *, abort: bool) -> bool:
        if self._dictation_watchdog is not None:
            try:
                self.root.after_cancel(self._dictation_watchdog)
            except tk.TclError:
                pass
            self._dictation_watchdog = None
        if abort:
            self.virtual_microphone.abort()
        session, self._dictation_session = self._dictation_session, None
        if session is None:
            return not abort
        try:
            if self.processor is None:
                raise RuntimeError("监听已经停止")
            self.processor._sender().stop_dictation(session)
            return True
        except Exception as exc:
            self._append_log(f"Codex 原生听写结束失败：{exc}")
            return False

    def _native_dictation_start_timeout(self) -> None:
        self._dictation_watchdog = None
        if self._dictation_session is None or self.voice_module.assembler.start is not None:
            return
        self._stop_native_dictation(abort=True)
        self.voice_status_value.set("手柄录音未响应")
        self._append_log("原生听写已取消：2.5 秒内没有收到手柄录音起始包")

    def start_voice_calibration(self) -> bool:
        if self.voice_module.assembler.start is not None:
            messagebox.showinfo('正在录音', '请等待当前录音结束后再开始校准。')
            return False
        if (self.processor is None or
                time.monotonic()-self.processor._last_sensor_batch_at > 1.5):
            messagebox.showinfo("请连接手柄", "校准需要连续六轴数据，请先连接手柄。")
            return False
        interval = (round(self.settings_window.tap_interval.get()*1000)
                    if self.settings_window is not None else None)
        self.voice_module.start_force_calibration(interval)
        return True

    def save_tap_calibration(self) -> bool:
        try:
            self.voice_module.save_force_calibration()
        except (OSError, ValueError) as exc:
            messagebox.showerror('未能保存校准', str(exc))
            return False
        return True

    def record_voice_calibration(self) -> bool:
        try:
            event = self.voice_module.record_calibration_sample()
        except (OSError, ValueError) as exc:
            self.emit("voice_calibration_error", str(exc))
            return False
        self.emit(
            "log",
            f"手动录入双敲：间隔 {event.interval_ms} ms，"
            f"冲击 {event.first_peak_dynamic_accel_g:.2f}/"
            f"{event.second_peak_dynamic_accel_g:.2f} g",
        )
        return True

    def cancel_voice_calibration(self) -> None:
        self.voice_module.cancel_calibration()

    def save_voice_pending(self) -> None:
        self.voice_module.set_pending(self.voice_text.get("1.0", "end-1c"))

    def clear_voice_pending(self) -> None:
        self.voice_module.clear_pending()

    def open_settings(self) -> None:
        self.ui.open_preferences()

    def _ensure_settings(self) -> None:
        if (
            self.settings_window is not None
            and self.settings_window.window.winfo_exists()
        ):
            return
        self.settings_window = DetectorSettingsWindow(
            self.root,
            self.detector_profile,
            self.send_settings_command,
            self.apply_detector_profile,
            motion_engine=self.motion_engine,
            raw_supported=self.firmware_supports_raw,
            message_store=self.message_store,
            visual_store=self.visual_store,
            apply_visual_settings=self.apply_visual_settings,
            voice_store=self.voice_store,
            apply_voice_settings=self.apply_voice_settings,
            start_voice_calibration=self.start_voice_calibration,
            record_voice_calibration=self.record_voice_calibration,
            cancel_voice_calibration=self.cancel_voice_calibration,
            voice_model_ready=lambda: self.voice_transcriber.ready,
            voice_tap_model_ready=lambda: self.voice_module.double_tap_template_ready,
            calibrate_mounting=self.open_mount_calibration,
            embedded_parent=self.ui.advanced_host,
            save_tap_calibration=self.save_tap_calibration,
            set_tap_interval=self.voice_module.set_tap_interval,
            power_enabled=self.power_store.enabled,
            apply_power_settings=self.apply_power_settings,
            power_status=self.power_status,
        )
        self.settings_window.speech_service.recording_active = lambda: self.voice_module.assembler.start is not None

    def simulate_whip(self) -> None:
        if self.worker_loop is None or self.processor is None:
            messagebox.showinfo("尚未监听", "请先启动监听，再模拟挥动。")
            return
        if self.armed.is_set() and not messagebox.askyesno(
            "确认实际发送",
            "软件当前已武装。模拟挥动会向 Codex 发送一条消息，是否继续？",
        ):
            return
        asyncio.run_coroutine_threadsafe(
            self.processor.handle(sample_event(), source="simulation"), self.worker_loop
        )

    def _handle_mouse_whip(self, _screen_point: tuple[float, float]) -> None:
        """Route a captured mouse strike through the same safe send pipeline."""
        if self.worker_loop is None or self.processor is None:
            self._append_log("鼠标抽打：监听未启动，本次只播放效果")
            return
        self._manual_sequence += 1
        event = replace(sample_event(), sequence=self._manual_sequence)
        asyncio.run_coroutine_threadsafe(
            self.processor.handle(event, source="mouse"), self.worker_loop
        )

    def toggle_arm(self) -> None:
        if self.mount_window is not None or self.ui.stage != "ready":
            self.armed.clear()
            self.arm_value.set(False)
            return
        if not self.arm_value.get():
            self.armed.clear()
            self.mode_value.set("安全监听")
            self.emit("log", "已解除武装；挥动只记录，不会向 Codex 输入")
            return

        self.arm_value.set(False)
        if not messagebox.askyesno(
            "武装实际发送",
            f"武装后，每次有效挥动都可能把 {self.settings.codex.target_app} 窗口置前并立即提交一条消息。\n\n"
            "目标不明确或输入框已有草稿时，软件会拒绝发送。是否继续？",
        ):
            return
        self.mode_value.set("检查中")
        self.arm_check.configure(state="disabled")
        self._arm_generation += 1
        threading.Thread(target=self._validate_arm, args=(self._arm_generation,), daemon=True).start()

    def _validate_arm(self, generation: int) -> None:
        try:
            result = create_live_sender(self.settings.codex).check_ready()
            self.emit("arm_result", (True, result, generation))
        except Exception as exc:
            self.emit("arm_result", (False, str(exc), generation))

    def check_codex(self) -> None:
        self.codex_value.set("正在检查")
        self.check_button.configure(state="disabled")

        selected = self.settings.codex
        def target_emit(kind, payload):
            self.emit('target_checked', (selected, kind, payload))
        def check() -> None:
            try:
                sender = create_live_sender(selected)
                try:
                    target = sender.locate_window()
                    target_emit("effect_target", (True, target))
                except Exception as exc:
                    target_emit("effect_target", (False, str(exc)))
                result = sender.check_ready()
                target_emit("codex_result", (True, result))
            except Exception as exc:
                target_emit("codex_result", (False, str(exc)))

        threading.Thread(target=check, daemon=True).start()

    def select_target_app(self, target: str) -> bool:
        if target not in ('Codex', 'Claude'):
            return False
        try:
            path = user_data_dir() / 'target-app.json'
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'target': target}), encoding='utf-8')
            temporary.replace(path)
        except OSError as exc:
            messagebox.showerror('无法保存目标', str(exc))
            return False
        self.armed.clear()
        self.arm_value.set(False)
        self._arm_generation += 1
        self.settings = replace(self.settings, codex=replace(self.settings.codex, target_app=target))
        self.mode_value.set('安全监听')
        self.effects.detach()
        self._effect_target_handle = None
        if self.processor is not None and self.worker_loop is not None:
            selected = self.settings.codex
            def update_sender():
                self.processor._codex_settings = selected
                self.processor._live_sender = None
            self.worker_loop.call_soon_threadsafe(update_sender)
        self.arm_check.configure(text=f'允许挥动后向 {target} 发送消息')
        self.check_button.configure(text=f'重新检查 {target}')
        self.check_codex()
        return True

    def _schedule_effect_target_refresh(self, delay_ms: int = 1500) -> None:
        if self.closing or self._effect_refresh_after is not None:
            return
        self._effect_refresh_after = self.root.after(
            delay_ms, self._refresh_effect_target
        )

    def _keep_awake_for_calibration(self) -> None:
        # A lease refreshed only during calibration, so a crash/disconnect can
        # never leave the board permanently inhibited from entering sleep.
        window = self.settings_window
        calibrating = (self.mount_window is not None or self.voice_module.calibration_active or
                       (window is not None and window.window.winfo_exists() and
                        window._stage in {'positive', 'negative'}))
        if (calibrating and self.ble_connected and supports_power_saving(self.firmware_version)
                and time.monotonic() - getattr(self, '_power_hold_at', 0) > 15):
            self.send_device_command('POWERHOLD')
            self._power_hold_at = time.monotonic()

    def _refresh_effect_target(self) -> None:
        self._effect_refresh_after = None
        if self.closing:
            return
        self._keep_awake_for_calibration()
        if self._effect_refresh_running:
            self._schedule_effect_target_refresh()
            return
        self._effect_refresh_running = True
        selected = self.settings.codex
        def locate() -> None:
            try:
                target = create_live_sender(selected).locate_window()
                self.emit("target_checked", (selected, "effect_target_auto", (True, target)))
            except Exception as exc:
                self.emit("target_checked", (selected, "effect_target_auto", (False, str(exc))))

        threading.Thread(
            target=locate, name="codex-whip-window-watch", daemon=True
        ).start()

    def _apply_effect_target(
        self, ok: bool, detail: object, *, automatic: bool
    ) -> None:
        if ok:
            target = detail
            if not isinstance(target, dict) or "handle" not in target:
                return
            handle = int(target["handle"])
            if handle != self._effect_target_handle:
                self.effects.attach(handle)
                self._effect_target_handle = handle
                self._append_log(
                    f"{self.settings.codex.target_app} 窗口已出现，黑色鞭子已自动显示"
                    if automatic
                    else f"黑色鞭子覆盖层已定位到 {self.settings.codex.target_app}"
                )
            return

        if self._effect_target_handle is not None:
            self.effects.detach()
            self._effect_target_handle = None
            self._append_log(f"{self.settings.codex.target_app} 窗口已隐藏或关闭，黑色鞭子已同步隐藏")
        elif not automatic:
            self._append_log(f"鞭子覆盖层等待 {self.settings.codex.target_app}：{detail}")

    def _append_log(self, line: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{stamp}  {line}\n")
        total_lines = int(self.log_text.index("end-1c").split(".")[0])
        if total_lines > 220:
            self.log_text.delete("1.0", f"{total_lines - 200}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _drain_events(self) -> None:
        if self._drain_after is not None:
            self.root.after_cancel(self._drain_after)
            self._drain_after = None
        try:
            # Never let a continuous producer monopolize Tk's event loop.
            # Coalesced telemetry normally keeps this well below the limit;
            # the cap is a final guard for bursts of lossless state events.
            for _event_index in range(96):
                kind, payload = self.events.get_nowait()
                self._hang_watchdog.note(f"ui-event:{kind}")
                if kind == 'target_checked':
                    selected, kind, payload = payload
                    if selected != self.settings.codex:
                        if kind == 'effect_target_auto':
                            self._effect_refresh_running = False
                            self._schedule_effect_target_refresh()
                        continue
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "profile_sync_started":
                    self._profile_ack_count = 0
                    self._append_log("正在同步本机检测阈值到开发板")
                elif kind == "scare_hotkey":
                    pass  # Removed feature; ignore stale queued events.
                elif kind == "ble":
                    self.ble_connected = str(payload) == "connected"
                    if not self.ble_connected:
                        self.firmware_version = ''
                        self._set_power_status('连接手柄后同步')
                        self.voice_module.pause_force_calibration()
                        self._profile_ack_count = 0
                        if self.mount_window is not None:
                            self._send_mount_command("disconnect", self.mount_window.token)
                    labels = {
                        "scanning": "正在扫描",
                        "connected": "已连接",
                        "not_found": "未发现设备",
                        "disconnected": "连接已断开",
                        "error": "连接错误",
                    }
                    self.ble_value.set(labels.get(str(payload), str(payload)))
                elif kind == "device":
                    message = payload
                    fields_text = ",".join(message.fields)
                    display = (
                        f"{message.kind},{fields_text}"
                        if fields_text
                        else message.kind
                    )
                    if message.kind == "PONG" and message.fields:
                        self.firmware_version = message.fields[0]
                        if supports_power_saving(self.firmware_version):
                            self.send_device_command(self.power_store.command())
                            self._set_power_status('等待手柄确认…')
                        else:
                            self._set_power_status('需要旧 XIAO 固件 0.6.1；当前尚未生效')
                        self.firmware_supports_settings = self._version_at_least(
                            self.firmware_version, (0, 3, 1)
                        )
                        self.firmware_supports_raw = self._version_at_least(
                            self.firmware_version, (0, 4, 0)
                        )
                        self.firmware_supports_voice = self._version_at_least(
                            self.firmware_version, (0, 5, 0)
                        )
                        self._append_log(f"设备：{display}")
                        if not self._version_at_least(self.firmware_version, (0, 6, 0)):
                            self._append_log("体感角度模式建议烧录固件 0.6.0：旧 RAW4 数据会丢失微小转动。")
                        if self.firmware_supports_settings:
                            if self.firmware_supports_raw:
                                raw_mode = "RAW,2" if self.motion_engine.trained else "RAW,1"
                                if self.send_device_command(raw_mode):
                                    self._append_log(
                                        "V3 个性化识别已接管六轴数据"
                                        if self.motion_engine.trained
                                        else "姿态角度跟随已开启；板端按转动、角位移和收腕识别抽打"
                                    )
                            if self.firmware_supports_voice:
                                voice_enabled = self.voice_store.settings.enabled
                                self.send_device_command(
                                    "VOICE,1" if voice_enabled else "VOICE,0"
                                )
                                if voice_enabled and self.voice_store.settings.input_mode == "transcription":
                                    self.prepare_voice_model()
                        else:
                            self._append_log(
                                "当前固件不支持手动录入；需要升级到 0.3.1 或更高版本"
                            )
                    elif (
                        message.kind == "CFG"
                        and len(message.fields) >= 2
                        and message.fields[0] == "OK"
                    ):
                        self._profile_ack_count += 1
                        if self._profile_ack_count == len(
                            self.detector_profile.commands()
                        ):
                            self._append_log("开发板已应用全部检测阈值")
                    elif message.kind == 'POWER' and len(message.fields) >= 2:
                        enabled, state = message.fields[:2]
                        confirmed = enabled == str(int(self.power_store.enabled))
                        text = ('省电中 · 移动手柄即可唤醒' if state == 'SLEEP' else
                                '已开启 · 静止 5 分钟后省电' if enabled == '1' else '已关闭')
                        self._set_power_status(text if confirmed else '手柄设置未同步，请重试开关')
                        self._append_log(f'省电模式：{text}')
                    elif message.kind == 'POWERERR':
                        self._set_power_status('省电设置或传感器恢复失败，请重启手柄并检查日志')
                        self._append_log(f'设备：{display}')
                    elif message.kind == "CFGVAL":
                        pass
                    else:
                        self._append_log(f"设备：{display}")
                    if (
                        message.kind == "LEARN"
                        and self.settings_window is not None
                        and self.settings_window.window.winfo_exists()
                    ):
                        self.settings_window.handle_device_status(message.fields)
                elif kind == "learning_sample":
                    sample = payload
                    self._append_log(
                        f"学习样本 #{sample.sequence}：{sample.peak_gyro_dps:.0f} dps，"
                        f"动态加速度 {sample.peak_dynamic_accel_g:.2f} g，"
                        f"角位移 {sample.angular_travel_deg:.0f}°"
                    )
                    if (
                        self.settings_window is not None
                        and self.settings_window.window.winfo_exists()
                    ):
                        self.settings_window.handle_sample(sample)
                elif kind == "sensor_pose":
                    if payload.auto_centered:
                        self.effects.auto_center_sensor()
                        self._append_log("静止三秒：已自动归中并更新手持零点")
                    self.effects.set_sensor_pose(payload)
                    if self.mount_window is not None:
                        if self.mount_window.stage == "review":
                            self.mount_window.show_pose(payload)
                elif kind == "mount_state":
                    if self.mount_window is not None and payload["token"] == self.mount_window.token:
                        self.mount_window.update_state(payload)
                elif kind == "mount_progress":
                    if self.mount_window is not None and payload[0] == self.mount_window.token:
                        self.mount_window.show_angle(payload[1])
                        self.mount_window.show_stability(payload[2])
                elif kind == "mount_closed":
                    if self.mount_window is not None and payload["token"] == self.mount_window.token:
                        self.mount_window.close(notify=False)
                    if payload["saved"]:
                        self.sensor_calibrate_button.configure(text="校准手持零点")
                        self.effects.reset_parking_to_center()
                        self._append_log("手柄安装方向已保存。以后换握姿只需校准手持零点；仍保持安全监听。")
                        self.last_event_value.set("方向学习完成 · 安装关系已保存")
                elif kind == "sensor_calibration":
                    ok, detail = payload
                    self.sensor_calibrate_button.configure(
                        state="normal", text="校准手持零点"
                    )
                    if ok:
                        self.effects.reset_parking_to_center()
                        self.last_event_value.set("手持零点已校准 · 屏幕基点位于 Codex 中心")
                    self._append_log(str(detail))
                elif kind == "whip":
                    if payload.get("source") != "mouse":
                        try:
                            self.effects.play()
                        except Exception as exc:
                            # Visual faults must not kill the BLE/UI event pump.
                            self._append_log(f"抽打画面异常（继续处理手柄事件）：{type(exc).__name__}")
                    summary = (
                        f"#{payload['sequence']}  ·  {payload['gyro']:.0f} dps  ·  "
                        f"{payload['accel']:.2f} g  ·  {payload['duration']} ms"
                    )
                    if (
                        payload["angular_travel"] is not None
                        and payload["direction_consistency"] is not None
                        and payload["dominant_axis"] is not None
                    ):
                        summary += (
                            f"  ·  {payload['angular_travel']:.0f}°  ·  "
                            f"方向 {payload['direction_consistency']:.0%}  ·  "
                            f"主轴 {payload['dominant_axis']:.0%}"
                        )
                    self.last_event_value.set(summary)
                    self._append_log(
                        f"{'鼠标抽打' if payload.get('source') == 'mouse' else ('V3 识别到挥鞭' if payload.get('source') == 'motion_v3' else '检测到 WHIP')} "
                        f"#{payload['sequence']}"
                    )
                    if payload["peak_gap"] is not None:
                        self._append_log(
                            f"V2 特征：角位移 {payload['angular_travel']:.1f}°，"
                            f"方向一致 {payload['direction_consistency']:.1%}，"
                            f"主轴 {payload['dominant_axis']:.1%}，"
                            f"峰值差 {payload['peak_gap']} ms，"
                            f"冲击变化 {payload['peak_jerk']:.1f} g/s"
                        )
                    self._append_log(
                        f"准备{'语音' if payload.get('voice_prompt') else ''}文案："
                        f"{payload['prompt']}"
                    )
                elif kind == "send_result":
                    self._append_log(str(payload.detail))
                elif kind == "send_error":
                    self._append_log(f"发送被拒绝：{payload}")
                    self.armed.clear()
                    self.arm_value.set(False)
                    self.mode_value.set("发送已暂停")
                elif kind == "worker_started":
                    self._append_log("监听服务已启动")
                elif kind == "worker_stopped":
                    self.ble_connected = False
                    if self.mount_window is not None:
                        self.mount_window.close(notify=False)
                    self.ble_value.set("已停止")
                    self.listen_button.configure(
                        text="启动监听", command=self.start_listening, state="normal"
                    )
                    self.sensor_calibrate_button.configure(
                        state="normal", text="校准手持零点"
                    )
                elif kind == "arm_result":
                    ok, detail, generation = payload
                    if (generation != self._arm_generation or self.mount_window is not None
                            or self.ui.stage != "ready"):
                        self.arm_check.configure(state="normal")
                        self.armed.clear()
                        self.arm_value.set(False)
                        continue
                    self.arm_check.configure(state="normal")
                    if ok:
                        handle = int(detail["handle"])
                        self.effects.attach(handle)
                        self._effect_target_handle = handle
                        self.armed.set()
                        self.arm_value.set(True)
                        self.mode_value.set("实际发送已武装")
                        self.codex_value.set("目标已就绪")
                        self._append_log(f"已武装：Codex PID {detail['pid']}")
                    else:
                        self.armed.clear()
                        self.arm_value.set(False)
                        self.mode_value.set("安全监听")
                        self._append_log(f"无法武装：{detail}")
                        messagebox.showwarning("无法武装", str(detail))
                elif kind == "codex_result":
                    ok, detail = payload
                    self.check_button.configure(state="normal")
                    if ok:
                        self.codex_value.set("已找到且输入框就绪")
                        self._append_log(f"{self.settings.codex.target_app} 已就绪：PID {detail['pid']}")
                    else:
                        self.codex_value.set("暂不可发送")
                        self._append_log(f"{self.settings.codex.target_app} 检查：{detail}")
                elif kind == "effect_target":
                    ok, detail = payload
                    self._apply_effect_target(ok, detail, automatic=False)
                elif kind == "effect_target_auto":
                    self._effect_refresh_running = False
                    ok, detail = payload
                    self._apply_effect_target(ok, detail, automatic=True)
                    self._schedule_effect_target_refresh()
                elif kind == "voice_match":
                    if payload.get("accepted"):
                        self._append_log(
                            "双敲轨迹模板通过："
                            f"距离 {payload['score']:.3f} / {payload['threshold']:.3f}"
                        )
                    elif "score" in payload:
                        self._append_log(
                            "忽略未通过模板的双敲候选："
                            f"距离 {payload['score']:.3f} / {payload['threshold']:.3f}"
                        )
                    else:
                        self._append_log(
                            f"忽略不完整的双敲候选：{payload.get('detail', '轨迹不足')}"
                        )
                elif kind == "voice_trigger":
                    if self.mount_window is not None or self.voice_module.calibration_active:
                        continue
                    event = payload
                    self._append_log(
                        f"检测到双敲：间隔 {event.interval_ms} ms，"
                        f"冲击 {event.first_peak_dynamic_accel_g:.2f}/"
                        f"{event.second_peak_dynamic_accel_g:.2f} g"
                    )
                    if not self.firmware_supports_voice:
                        self.voice_status_value.set("需要 0.5.0 固件")
                        self._append_log("双敲录音未启动：当前固件不支持麦克风传输")
                    elif (self.voice_store.settings.input_mode == "transcription"
                          and not self.voice_transcriber.ready):
                        self.voice_status_value.set("识别服务尚未就绪")
                        self.prepare_voice_model()
                        self._append_log("双敲录音未启动：请检查语音识别服务配置")
                    else:
                        voice = self.voice_store.settings
                        if voice.input_mode == "virtual_microphone":
                            if self.voice_module.native_draft_pending:
                                self.voice_status_value.set("已有听写草稿，挥鞭发送或手动清空")
                                self._append_log("忽略双敲：Codex 输入框仍有待发送听写草稿")
                                continue
                            try:
                                if self.processor is None:
                                    raise VirtualMicrophoneError("监听尚未启动")
                                device = self.virtual_microphone.start(voice.recording_gain)
                                self._dictation_session = self.processor._sender().start_dictation()
                                self._append_log(f"Codex 原生听写已启动：{device.name}")
                            except Exception as exc:
                                self.virtual_microphone.abort()
                                self._dictation_session = None
                                self.voice_status_value.set("原生听写启动失败")
                                self._append_log(f"原生听写：{exc}")
                                continue
                        if self.send_device_command(f"VOICE,START,{voice.silence_ms},{voice.max_recording_ms}"):
                            self.voice_status_value.set("正在启动录音")
                            if voice.input_mode == "virtual_microphone":
                                self._dictation_watchdog = self.root.after(
                                    2500, self._native_dictation_start_timeout)
                        elif voice.input_mode == "virtual_microphone":
                            self._stop_native_dictation(abort=True)
                elif kind == "voice_state":
                    state = str(payload.get("state", ""))
                    if self.settings_window is not None:
                        card = getattr(self.settings_window, 'speech_service', None)
                        if card is not None and card.card.winfo_exists():
                            card.set_recording(state == 'recording')
                    if state == "recording":
                        if self._dictation_watchdog is not None:
                            self.root.after_cancel(self._dictation_watchdog)
                            self._dictation_watchdog = None
                        system_beep("start")
                        replacing = bool(payload.get("replacing"))
                        self.voice_status_value.set(
                            "正在重新录音，请说话" if replacing else "正在录音，请说话"
                        )
                        self._append_log(
                            "已丢弃上一条语音候选，正在重新录音"
                            if replacing
                            else "麦克风录音已开始；静音后自动识别"
                        )
                    elif state == "recognizing":
                        system_beep("ok")
                        self.voice_status_value.set("正在本地识别")
                        self._append_log("录音接收完成，正在本地识别中文")
                    elif state == "ready":
                        self.voice_status_value.set("文字已就绪，等待下一鞭")
                    elif state == "dictation_ready":
                        if self._stop_native_dictation(abort=False):
                            self.voice_module.mark_native_draft_ready()
                            self.voice_status_value.set("文字已在 Codex，等待下一鞭")
                            self._append_log("Codex 原生听写已结束；下一鞭会发送输入框草稿")
                    elif state == "empty":
                        self.voice_status_value.set("未检测到真实说话，内容为空")
                        self._append_log(
                            "本次没有检测到可用人声，不会发送内容；可再次双敲重录"
                        )
                elif kind == "voice_pending":
                    value = "" if payload is None else str(payload)
                    self.voice_text.delete("1.0", "end")
                    if value:
                        self.voice_text.insert("1.0", value)
                        self.voice_status_value.set("文字已就绪，等待下一鞭")
                        self._append_log(f"下一鞭语音：{value}")
                    elif self.voice_store.settings.enabled:
                        self.voice_status_value.set("等待双敲手柄")
                elif kind == "voice_error":
                    self._stop_native_dictation(abort=True)
                    system_beep("error")
                    self.voice_status_value.set("录音或识别失败")
                    self._append_log(f"语音模块：{payload}")
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        self.settings_window.set_voice_runtime_status(str(payload))
                elif kind == "voice_model_progress":
                    done, total = payload
                    percent = min(100, round(done * 100 / max(1, total)))
                    self.voice_status_value.set(f"正在下载本地模型 {percent}%")
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        self.settings_window.set_voice_runtime_status(
                            f"正在下载并校验本地模型：{percent}%"
                        )
                elif kind == "voice_model_ready":
                    self._voice_model_preparing = False
                    if self.voice_store.settings.input_mode == "transcription":
                        self.voice_status_value.set(
                            "等待双敲手柄"
                            if self.voice_store.settings.enabled
                            else "已关闭（可在设置中启用）"
                        )
                    self._append_log("语音识别服务已就绪")
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        self.settings_window.set_voice_runtime_status("语音识别服务已就绪")
                elif kind == "voice_model_error":
                    self._voice_model_preparing = False
                    self.voice_status_value.set("模型准备失败")
                    self._append_log(f"本地语音模型准备失败：{payload}")
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        self.settings_window.set_voice_runtime_status(f"模型准备失败：{payload}")
                elif kind == 'tap_calibration_state':
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        if (self.voice_module._force_calibration is not None and
                                payload.get('session') == id(self.voice_module._force_calibration)):
                            self.settings_window.handle_tap_calibration(payload)
                elif kind == 'tap_calibration_saved':
                    self._append_log('双敲力度校准已保存；不再使用旧轨迹匹配。')
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        self.settings_window.refresh_voice_settings(payload)
                elif kind == "voice_calibration":
                    done = int(payload.get("done", 0))
                    total = int(payload.get("total", 5))
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        self.settings_window.handle_voice_calibration(done, total)
                elif kind == "voice_calibration_error":
                    self._append_log(f"双敲手动录入失败：{payload}")
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        self.settings_window.set_voice_calibration_error(str(payload))
                elif kind == "voice_calibration_done":
                    learned = payload
                    self._append_log(
                        "双敲轨迹模板已启用："
                        f"候选冲击 {self.voice_module.double_tap_profile.candidate_impact_g:.2f} g，"
                        f"候选间隔 {self.voice_module.double_tap_profile.candidate_min_interval_ms}–"
                        f"{self.voice_module.double_tap_profile.candidate_max_interval_ms} ms"
                    )
                    if self.settings_window is not None and self.settings_window.window.winfo_exists():
                        self.settings_window.refresh_voice_settings(learned)
                self.ui.observe(kind, payload)
        except queue.Empty:
            pass
        if not self.closing:
            delay_ms = 1 if not self.events.empty() else 16
            self._drain_after = self.root.after(delay_ms, self._drain_events)

    def close(self) -> None:
        self.closing = True
        if self._hang_heartbeat_after is not None:
            try:
                self.root.after_cancel(self._hang_heartbeat_after)
            except tk.TclError:
                pass
            self._hang_heartbeat_after = None
        self._hang_watchdog.stop()
        self._stop_native_dictation(abort=True)
        for callback in (self._drain_after, self._listen_after, self._check_after):
            if callback is not None:
                self.root.after_cancel(callback)
        self.armed.clear()
        if self.mount_window is not None:
            self.mount_window.close()
        if self._scare_hotkey is not None:
            self._scare_hotkey.stop()
            self._scare_hotkey = None
        if self._effect_refresh_after is not None:
            try:
                self.root.after_cancel(self._effect_refresh_after)
            except tk.TclError:
                pass
            self._effect_refresh_after = None
        if (
            self.settings_window is not None
            and self.settings_window.window.winfo_exists()
        ):
            self.settings_window.close()
        if self.worker_loop is not None and self.worker_stop is not None:
            self.worker_loop.call_soon_threadsafe(self.worker_stop.set)
        self.effects.close()
        self.ui.close()
        self.root.destroy()


def main() -> int:
    if "--ui-smoke" in sys.argv:
        from .ui_smoke import main as smoke_main
        return smoke_main(sys.argv[sys.argv.index("--ui-smoke") + 1:])
    migration = import_bundled_profile_once()
    config_path = find_config_path()
    try:
        settings = load_settings(config_path)
    except Exception as exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("配置文件错误", f"无法读取配置：\n{exc}")
        root.destroy()
        return 2

    root = tk.Tk()
    window = CodexWhipWindow(root, settings, config_path)
    if migration.imported:
        window.emit("log", f"已继承 Windows 数据：{len(migration.imported)} 个文件")
    for error in migration.errors:
        window.emit("log", f"数据继承失败：{error}")
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
