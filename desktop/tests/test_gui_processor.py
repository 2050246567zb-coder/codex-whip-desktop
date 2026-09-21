import asyncio
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

from codex_whip.calibration import LearningSample
from codex_whip.gui import (
    CodexWhipWindow,
    GuiEventProcessor,
    NativeDictationJob,
    UiEventBuffer,
    UiHangWatchdog,
)
from codex_whip.models import DeviceMessage, RawMotionBatch, RawMotionFrame, WhipEvent
from codex_whip.senders.base import SendResult
from codex_whip.settings import Settings
from codex_whip.voice import VoiceModule, VoiceSettingsStore
from codex_whip.visual_settings import VisualSettings, VisualSettingsStore


def test_safe_gui_mode_records_whip_without_sending() -> None:
    emitted: list[tuple[str, object]] = []
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda kind, payload: emitted.append((kind, payload))
    )

    asyncio.run(processor.handle(WhipEvent(7, 980.0, 3.2, 120)))

    assert emitted[0][0] == "whip"
    assert emitted[0][1]["sequence"] == 7  # type: ignore[index]
    assert emitted[1][0] == "send_result"
    assert emitted[1][1].sent is False  # type: ignore[union-attr]


def test_ui_event_buffer_coalesces_motion_and_audio_without_losing_actions() -> None:
    events = UiEventBuffer()
    for value in range(10_000):
        events.put(("sensor_pose", value))
        events.put(("ui_audio_level", value / 10_000))
    events.put(("voice_state", {"state": "recording"}))

    assert events.get_nowait() == ("sensor_pose", 9_999)
    assert events.get_nowait() == ("ui_audio_level", 0.9999)
    assert events.get_nowait() == ("voice_state", {"state": "recording"})
    assert events.empty()


def test_ui_hang_watchdog_uses_heartbeat_and_dump_cooldown(tmp_path) -> None:
    watchdog = UiHangWatchdog(
        tmp_path / "hang.log", timeout_seconds=3.0, repeat_seconds=10.0
    )
    watchdog._heartbeat_at = 10.0
    watchdog._last_dump_at = 0.0

    assert not watchdog.overdue(12.99)
    assert watchdog.overdue(13.0)
    watchdog._last_dump_at = 12.0
    assert not watchdog.overdue(20.0)
    assert watchdog.overdue(22.0)


def test_ui_hang_watchdog_persists_thread_dump(tmp_path) -> None:
    path = tmp_path / "hang.log"
    watchdog = UiHangWatchdog(
        path, timeout_seconds=0.05, repeat_seconds=10.0
    )
    watchdog.note("voice-state:recording")
    watchdog.start()
    try:
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and not path.exists():
            time.sleep(0.02)
    finally:
        watchdog.stop()

    text = path.read_text(encoding="utf-8")
    assert "version=2.2.52" in text
    assert "context=voice-state:recording" in text
    assert "Current thread" in text
    assert "_run" in text


def test_native_dictation_start_and_stop_never_run_on_ui_thread() -> None:
    emitted: list[tuple[str, object]] = []
    started = threading.Event()
    stopped = threading.Event()
    calls: list[tuple[str, int]] = []
    ui_thread = threading.get_ident()

    class Sender:
        def start_dictation(self):
            calls.append(("start", threading.get_ident()))
            return object()

        def stop_dictation(self, _session):
            calls.append(("stop", threading.get_ident()))

    class Microphone:
        def start(self, _gain):
            calls.append(("microphone", threading.get_ident()))
            return SimpleNamespace(name="Test Cable")

        def abort(self):
            calls.append(("abort", threading.get_ident()))

    def emit(kind, payload):
        emitted.append((kind, payload))
        if kind == "native_dictation_started":
            started.set()
        elif kind == "native_dictation_stopped":
            stopped.set()

    job = NativeDictationJob(Sender(), Microphone(), 2.0, 7, emit)
    job.start()
    assert started.wait(1)
    job.request_stop(abort=False)
    assert stopped.wait(1)

    worker_threads = {thread_id for _name, thread_id in calls}
    assert len(worker_threads) == 1
    assert ui_thread not in worker_threads
    assert [name for name, _thread_id in calls] == ["start", "microphone", "stop"]
    assert emitted[0][0] == "native_dictation_started"
    assert emitted[-1][0] == "native_dictation_stopped"


def test_gui_processor_reports_device_messages() -> None:
    emitted: list[tuple[str, object]] = []
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda kind, payload: emitted.append((kind, payload))
    )

    asyncio.run(processor.handle(DeviceMessage("PONG", ("0.1.0",), "PONG,0.1.0")))

    assert emitted == [
        ("device", DeviceMessage("PONG", ("0.1.0",), "PONG,0.1.0"))
    ]


def test_gui_processor_routes_learning_samples_without_sending() -> None:
    emitted: list[tuple[str, object]] = []
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda kind, payload: emitted.append((kind, payload))
    )
    sample = LearningSample(1, "CAPTURED", 800, 1.2, 140, 70, 0.4, 0.6, 20, 200)

    asyncio.run(processor.handle(sample))

    assert emitted == [("learning_sample", sample)]


def test_firmware_version_gate() -> None:
    assert CodexWhipWindow._version_at_least("0.3.1", (0, 3, 1))
    assert CodexWhipWindow._version_at_least("0.4", (0, 3, 1))
    assert not CodexWhipWindow._version_at_least("0.3.0", (0, 3, 1))
    assert not CodexWhipWindow._version_at_least("broken", (0, 3, 1))


def test_gui_applies_visual_damage_frequency_immediately(tmp_path) -> None:
    window = object.__new__(CodexWhipWindow)
    window.visual_store = VisualSettingsStore(tmp_path / "visual-settings.json")
    applied: list[int] = []
    emitted: list[tuple[str, object]] = []
    window.effects = type(
        "Effects",
        (),
        {"set_damage_interval": lambda _self, value: applied.append(value),
         "set_feedback": lambda _self, **kwargs: None},
    )()
    window.emit = lambda kind, payload: emitted.append((kind, payload))

    result = window.apply_visual_settings(VisualSettings(strikes_per_wound=4))

    assert result is True
    assert window.visual_store.settings.strikes_per_wound == 4
    assert applied == [4]
    assert emitted[-1] == (
        "log",
        "视觉设置已保存：每 4 次显示伤口",
    )


def test_gui_processor_exposes_whip2_shape_metrics() -> None:
    emitted: list[tuple[str, object]] = []
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda kind, payload: emitted.append((kind, payload))
    )
    event = WhipEvent(8, 1200.0, 4.1, 144, 91.5, 0.76, 0.64, 28, 215.0)

    asyncio.run(processor.handle(event))

    payload = emitted[0][1]
    assert payload["angular_travel"] == 91.5  # type: ignore[index]
    assert payload["direction_consistency"] == 0.76  # type: ignore[index]
    assert payload["dominant_axis"] == 0.64  # type: ignore[index]
    assert payload["peak_gap"] == 28  # type: ignore[index]


def test_gui_processor_marks_mouse_whip_source() -> None:
    emitted: list[tuple[str, object]] = []
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda kind, payload: emitted.append((kind, payload))
    )

    asyncio.run(processor.handle(WhipEvent(1_000_001, 980, 3.2, 120), source="mouse"))

    payload = emitted[0][1]
    assert emitted[0][0] == "whip"
    assert payload["source"] == "mouse"  # type: ignore[index]


def test_gui_processor_routes_raw_imu_batches_to_screen_pose() -> None:
    emitted: list[tuple[str, object]] = []
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda kind, payload: emitted.append((kind, payload))
    )
    batch = RawMotionBatch(
        1,
        1000,
            (
                RawMotionFrame(1000, 0, 0, 0, 0, 0, 1),
                RawMotionFrame(1010, 0, 420, 180, 0.35, 0.10, 0.92),
                RawMotionFrame(1020, 0, 420, 180, 0.35, 0.10, 0.92),
            ),
    )

    asyncio.run(processor.handle(batch))

    assert emitted[0][0] == "sensor_pose"
    pose = emitted[0][1]
    assert abs(pose.offset_x) > 0.1  # type: ignore[union-attr]
    assert abs(pose.angle_degrees) > 0.1  # type: ignore[union-attr]


def test_gui_processor_calibrates_recent_sensor_pose() -> None:
    emitted: list[tuple[str, object]] = []
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda kind, payload: emitted.append((kind, payload))
    )
    raw = RawMotionBatch(
        1,
        1000,
        tuple(RawMotionFrame(t, 0, 0, 0, 0, 0.6, 0.8) for t in range(1000, 1610, 10)),
    )
    asyncio.run(processor.handle(raw))
    emitted.clear()

    processor.calibrate_sensor_neutral()

    assert emitted[0][0] == "sensor_pose"
    pose = emitted[0][1]
    assert pose.offset_x == 0.0  # type: ignore[union-attr]
    assert pose.offset_y == 0.0  # type: ignore[union-attr]
    assert emitted[1] == (
        "sensor_calibration",
        (True, "手持零点已校准；当前姿态对应 Codex 窗口中心。"),
    )


def test_processor_enables_idle_center_only_outside_direction_wizard():
    emitted = []
    processor = GuiEventProcessor(Settings(), threading.Event(), lambda *e: emitted.append(e))
    def batch(start, end):
        return RawMotionBatch(1, start, tuple(RawMotionFrame(t, 0, 0, 0, 0, 0, 1)
                                             for t in range(start, end, 10)))
    asyncio.run(processor.handle(batch(0, 3500)))
    assert any(k == 'sensor_pose' and v.auto_centered for k, v in emitted)
    assert not any(k in ('whip', 'send_result') for k, _ in emitted)
    emitted.clear()
    processor.mount_command('open', 'idle-test')
    asyncio.run(processor.handle(batch(3500, 10500)))
    assert all(not v.auto_centered for k, v in emitted if k == 'sensor_pose')
    processor.mount_command('cancel', 'idle-test')
    emitted.clear()
    asyncio.run(processor.handle(batch(10500, 14000)))
    assert any(k == 'sensor_pose' and v.auto_centered for k, v in emitted)


def test_auto_center_never_changes_raw_input_or_creates_whip_events():
    from unittest.mock import Mock
    engine = Mock()
    engine.feed_batch.return_value = None
    emitted = []
    processor = GuiEventProcessor(Settings(), threading.Event(), lambda *e: emitted.append(e), motion_engine=engine)
    samples = tuple(RawMotionFrame(t, 0, 0, 0, 0, 0, 1) for t in range(0, 3600, 10))
    batch = RawMotionBatch(1, 0, samples)
    asyncio.run(processor.handle(batch))
    assert engine.feed_batch.call_args.args[0] is batch
    assert batch.frames == samples
    assert any(k == 'sensor_pose' and v.auto_centered for k, v in emitted)
    assert not any(k in ('whip', 'send_result', 'send_error') for k, _ in emitted)


def test_pending_voice_text_has_priority_and_clears_only_after_send(tmp_path) -> None:
    emitted: list[tuple[str, object]] = []
    voice = VoiceModule(
        VoiceSettingsStore(tmp_path / "voice.json"),
        lambda kind, payload: emitted.append((kind, payload)),
    )
    voice.set_pending("完成语音指定的关键任务")
    emitted.clear()
    armed = threading.Event()
    armed.set()
    processor = GuiEventProcessor(
        Settings(), armed, lambda kind, payload: emitted.append((kind, payload)),
        voice_module=voice,
    )

    class Sender:
        def send(self, prompt, _event):
            assert prompt == "完成语音指定的关键任务"
            return SendResult(True, "已发送")

    processor._live_sender = Sender()
    asyncio.run(processor.handle(WhipEvent(44, 900, 3.0, 120)))

    whip_payload = next(payload for kind, payload in emitted if kind == "whip")
    assert whip_payload["voice_prompt"] is True
    assert voice.pending_text is None


def test_native_dictation_draft_is_submitted_without_inserting_prompt(tmp_path) -> None:
    emitted = []
    voice = VoiceModule(VoiceSettingsStore(tmp_path / "voice.json"), lambda *event: emitted.append(event))
    voice.mark_native_draft_ready()
    armed = threading.Event()
    armed.set()
    processor = GuiEventProcessor(Settings(), armed, lambda *event: emitted.append(event), voice_module=voice)

    class Sender:
        def send(self, *_args):
            raise AssertionError("native dictation must not insert a configured prompt")
        def submit_existing(self, event):
            assert event.sequence == 45
            return SendResult(True, "sent native draft")

    processor._live_sender = Sender()
    asyncio.run(processor.handle(WhipEvent(45, 900, 3.0, 120)))
    assert voice.native_draft_pending is False
    payload = next(payload for kind, payload in emitted if kind == "whip")
    assert payload["native_dictation"] is True


def _voice_motion_frames(*, second_tap: bool) -> tuple[RawMotionFrame, ...]:
    frames = [RawMotionFrame(time, 0, 0, 0, 0, 0, 1) for time in range(0, 310, 10)]
    frames.extend((
        RawMotionFrame(310, 120, 0, 0, 0, 0, 3.2),
        RawMotionFrame(320, 160, 0, 0, 0, 0, 3.8),
        RawMotionFrame(330, 0, 0, 0, 0, 0, 1),
    ))
    if second_tap:
        frames.extend(RawMotionFrame(time, 0, 0, 0, 0, 0, 1)
                      for time in range(340, 550, 10))
        frames.extend((
            RawMotionFrame(550, 140, 0, 0, 0, 0, 3.4),
            RawMotionFrame(560, 180, 0, 0, 0, 0, 4.0),
            RawMotionFrame(570, 0, 0, 0, 0, 0, 1),
        ))
    return tuple(frames)


def _enabled_voice(tmp_path, emitted):
    store = VoiceSettingsStore(tmp_path / "voice.json")
    store.update(replace(store.settings, enabled=True))
    return VoiceModule(store, lambda *event: emitted.append(event))


def test_first_tap_candidate_does_not_swallow_firmware_whip(tmp_path) -> None:
    emitted: list[tuple[str, object]] = []
    voice = _enabled_voice(tmp_path, emitted)
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda *event: emitted.append(event),
        voice_module=voice,
    )
    batch = RawMotionBatch(1, 0, _voice_motion_frames(second_tap=False))

    asyncio.run(processor.handle(batch))
    assert voice.detector.suppress_whip  # a possible first tap is pending
    asyncio.run(processor.handle(WhipEvent(46, 900, 3.0, 120)))

    assert any(kind == "whip" for kind, _payload in emitted)


def test_completed_double_tap_owns_its_firmware_motion_tail(tmp_path) -> None:
    emitted: list[tuple[str, object]] = []
    voice = _enabled_voice(tmp_path, emitted)
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda *event: emitted.append(event),
        voice_module=voice,
    )

    asyncio.run(processor.handle(
        RawMotionBatch(1, 0, _voice_motion_frames(second_tap=True))
    ))
    assert voice.blocks_device_whip
    asyncio.run(processor.handle(WhipEvent(47, 900, 3.0, 120)))

    assert not any(kind == "whip" for kind, _payload in emitted)
    assert any(kind == "log" and "已完成双敲" in str(payload)
               for kind, payload in emitted)


def test_v3_still_sees_a_lone_first_tap_candidate(tmp_path) -> None:
    emitted: list[tuple[str, object]] = []
    voice = _enabled_voice(tmp_path, emitted)
    engine = Mock()
    engine.feed_batch.return_value = None
    processor = GuiEventProcessor(
        Settings(), threading.Event(), lambda *event: emitted.append(event),
        motion_engine=engine, voice_module=voice,
    )
    batch = RawMotionBatch(1, 0, _voice_motion_frames(second_tap=False))

    asyncio.run(processor.handle(batch))

    engine.feed_batch.assert_called_once_with(batch)


def test_retired_shortcut_never_registers():
    from unittest.mock import Mock
    window = object.__new__(CodexWhipWindow)
    listener = Mock()
    window._scare_hotkey = listener
    assert window._replace_scare_hotkey(VisualSettings(scare_enabled=True), log=True)
    listener.stop.assert_called_once()
    assert window._scare_hotkey is None
