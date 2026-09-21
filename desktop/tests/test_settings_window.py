import tkinter as tk

import pytest

from codex_whip.calibration import DetectorProfile, LearningSample
from codex_whip.motion_v3 import MotionEngine
from codex_whip.messages import MessageProfileStore
from codex_whip.settings_window import DetectorSettingsWindow
from codex_whip.voice import VoiceSettingsStore
from codex_whip.visual_settings import VisualSettings, VisualSettingsStore


def test_action_button_proportions_survive_disabled_state(root):
    from tkinter import font as tkfont
    from codex_whip.settings_style import ActionButton
    calls = []
    buttons = [ActionButton(root,text,lambda: calls.append(True),primary=i!=1)
               for i,text in enumerate(('开始校准','取消','保存并完成'))]
    try:
        for b in buttons:
            b.pack()
        root.update_idletasks()
        sizes = [(b.winfo_reqwidth(),b.winfo_reqheight()) for b in buttons]
        assert len({h for w,h in sizes}) == 1
        for b,(w,h) in zip(buttons,sizes):
            font = tkfont.Font(root=b,font=b.cget('font'))
            assert b._paint_key[0]-font.measure(b.cget('text')) == 28
        save = buttons[-1]
        save.configure(state='disabled')
        root.update_idletasks()
        assert (save.winfo_reqwidth(),save.winfo_reqheight()) == sizes[-1]
        assert save._disabled_surface.winfo_manager() == 'place'
        save.invoke()
        assert calls == []
        save.configure(state='normal')
        assert not save._disabled_surface.winfo_manager()
        save.invoke()
        assert calls == [True]
    finally:
        for b in buttons:
            b.destroy()


def sample(sequence: int) -> LearningSample:
    return LearningSample(
        sequence=sequence,
        result="CAPTURED",
        peak_gyro_dps=900.0,
        peak_dynamic_accel_g=1.2,
        duration_ms=140,
        angular_travel_deg=70.0,
        direction_consistency=0.4,
        dominant_axis_ratio=0.6,
        peak_gap_ms=25,
        peak_jerk_gps=200.0,
    )


def test_power_switch_is_in_handle_group_and_reverts_failed_save(root, tmp_path):
    values=[]
    window=DetectorSettingsWindow(root, DetectorProfile(), lambda _:True, lambda _:True,
        message_store=MessageProfileStore(('test',),path=tmp_path/'messages.json'),
        voice_store=VoiceSettingsStore(tmp_path/'voice.json'),
        visual_store=VisualSettingsStore(tmp_path/'visual.json'),
        apply_power_settings=lambda value: values.append(value) or False)
    try:
        window.show_group('calibration')
        assert window._power_panel.winfo_manager()=='pack'
        window.power_enabled.set(True)
        window._toggle_power()
        assert values==[True] and not window.power_enabled.get()
        window.show_group('input')
        assert not window._power_panel.winfo_manager()
        window.show_group('general')
        assert not window._power_panel.winfo_manager()
    finally:
        window.close()


@pytest.fixture(scope="module")
def root() -> tk.Tk:
    value = tk.Tk()
    value.withdraw()
    yield value
    value.destroy()


def test_learning_counts_only_after_manual_take(root: tk.Tk) -> None:
    commands: list[str] = []
    window = DetectorSettingsWindow(
        root,
        DetectorProfile(),
        lambda command: commands.append(command) or True,
        lambda _profile: True,
    )
    try:
        window.start_positive_learning()
        window.handle_sample(sample(99))
        assert len(window._positive_samples) == 0

        for sequence in range(15):
            window.request_record()
            assert commands[-1] == "LEARN,TAKE"
            window.handle_sample(sample(sequence))

        assert len(window._positive_samples) == 15
        assert window._stage == "positive_done"
        assert commands.count("LEARN,TAKE") == 15
        assert commands[-1] == "LEARN,STOP"
    finally:
        window.close()


def test_empty_take_does_not_increment_progress(root: tk.Tk) -> None:
    commands: list[str] = []
    window = DetectorSettingsWindow(
        root,
        DetectorProfile(),
        lambda command: commands.append(command) or True,
        lambda _profile: True,
    )
    try:
        window.start_positive_learning()
        window.request_record()
        window.handle_device_status(("EMPTY",))

        assert len(window._positive_samples) == 0
        assert not window._awaiting_record
        assert str(window.record_button["state"]) == "normal"
        assert "未计数" in window.learning_status.get()
    finally:
        window.close()


def test_v3_settings_builds_complete_learning_controls(
    root: tk.Tk, tmp_path
) -> None:
    commands: list[str] = []
    engine = MotionEngine(tmp_path / "motion-v3.json")
    window = DetectorSettingsWindow(
        root,
        DetectorProfile(),
        lambda command: commands.append(command) or True,
        lambda _profile: True,
        motion_engine=engine,
        raw_supported=True,
    )
    try:
        assert window.positive_button.winfo_exists()
        assert window.record_button.winfo_exists()
        assert window.tolerance_scale.winfo_exists()
        assert window.save_button.winfo_exists()

        window.start_positive_learning()
        assert commands[-1] == "RAW,2"
        assert window._stage == "positive"
        assert str(window.record_button["state"]) == "normal"
    finally:
        window.close()


def test_message_settings_build_cards_and_save_order(root: tk.Tk, tmp_path) -> None:
    store = MessageProfileStore(("first", "second"), tmp_path / "messages.json")
    window = DetectorSettingsWindow(
        root,
        DetectorProfile(),
        lambda _command: True,
        lambda _profile: True,
        message_store=store,
    )
    try:
        assert len(window._message_widgets) == 2
        window.message_order.set("sequential")
        window._message_widgets[0].delete("1.0", "end")
        window._message_widgets[0].insert("1.0", "updated")
        window._save_messages()

        assert store.profile.order == "sequential"
        assert store.profile.messages == ("updated", "second")
    finally:
        window.close()


def test_visual_settings_adjust_and_save_wound_frequency(root: tk.Tk, tmp_path) -> None:
    message_store = MessageProfileStore(("first",), tmp_path / "messages.json")
    visual_store = VisualSettingsStore(tmp_path / "visual-settings.json")

    def apply(settings: VisualSettings) -> bool:
        visual_store.update(settings)
        return True

    window = DetectorSettingsWindow(
        root,
        DetectorProfile(),
        lambda _command: True,
        lambda _profile: True,
        message_store=message_store,
        visual_store=visual_store,
        apply_visual_settings=apply,
    )
    try:
        assert window.visual_frequency_slider.winfo_exists()
        assert not hasattr(window, "visual_scare_hotkey_entry")
        window.visual_strikes_per_wound.set("5")
        window._save_visual_settings()
        assert visual_store.settings.strikes_per_wound == 5
        assert not visual_store.settings.scare_enabled
        assert "每 5 次" in window.visual_status.get()
    finally:
        window.close()


def test_tap_force_sliders_and_one_pair_auto_setting(root: tk.Tk, tmp_path) -> None:
    starts: list[bool] = []
    cancels: list[bool] = []
    applied = []
    store = VoiceSettingsStore(tmp_path / "voice.json")
    def apply(settings):
        store.update(settings)
        applied.append(settings)
        return True
    window = DetectorSettingsWindow(
        root,
        DetectorProfile(),
        lambda _command: True,
        lambda _profile: True,
        voice_store=store,
        apply_voice_settings=apply,
        start_voice_calibration=lambda: starts.append(True) or True,
        cancel_voice_calibration=lambda: cancels.append(True),
        voice_model_ready=lambda: True,
    )
    try:
        window.tap_minimum_slider.set(1.2)
        window.tap_maximum_slider.set(3.8)
        assert window._commit_tap_range()
        assert applied[-1].tap_light_g == 1.2
        assert applied[-1].tap_heavy_g == 3.8
        assert applied[-1].impact_dynamic_accel_g == 1.2
        assert '1.20–3.80 g' in window.tap_range_status.get()
        window._begin_voice_calibration()
        assert starts == [True]
        assert window._voice_calibrating
        window.handle_tap_range_capture(dict(stage='capture', live_strengths=(2.0,)))
        assert '第一下 2.00 g' in window.tap_range_status.get()
        saved = window.handle_tap_range_capture(dict(
            stage='done', live_strengths=(2.0, 2.0), average_g=2.0,
            suggested_min_g=1.4, suggested_max_g=2.6))
        assert saved
        assert window.tap_minimum.get() == 1.4
        assert window.tap_maximum.get() == 2.6
        assert applied[-1].tap_light_g == 1.4
        assert applied[-1].tap_heavy_g == 2.6
        window._select_section('voice')
        assert not window._voice_calibrating
    finally:
        window.close()
    assert cancels == []


def test_untrained_sensitivity_updates_real_thresholds_and_reopens(root, tmp_path, monkeypatch):
    from codex_whip.calibration import save_profile, load_profile
    monkeypatch.setenv('CODEX_WHIP_DATA_DIR', str(tmp_path))
    applied = []
    def apply(profile):
        save_profile(profile)
        applied.append(profile)
        return False  # disconnected: saved now, sync later
    window = DetectorSettingsWindow(root, DetectorProfile(), lambda _: False, apply)
    try:
        window.tolerance_scale.set(150)
        window._commit_tolerance()
        assert applied[-1].confirm_gyro_dps < DetectorProfile().confirm_gyro_dps
        assert not window._learning_status_label.winfo_manager()
        assert window.tolerance_scale.pack_info()['fill'] == 'x'
    finally:
        window.close()
    window = DetectorSettingsWindow(root, load_profile(), lambda _: False, apply)
    try:
        assert window.tolerance_value.get() == 150
        window.tolerance_scale.set(100)
        window._commit_tolerance()
        assert applied[-1] == DetectorProfile()
    finally:
        window.close()
