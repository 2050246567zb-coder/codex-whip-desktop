import time
from types import SimpleNamespace
import tkinter as tk
from unittest.mock import Mock
import pytest
from PIL import Image

from codex_whip.gui import CodexWhipWindow
from codex_whip.interface import asset_path, sleep_dot_count
from codex_whip.mount_profile import MountingProfile, save_mounting_profile
from codex_whip.sensor_pose import SensorPose
from codex_whip.settings import Settings


def test_clock_tilts_and_exits_when_pointer_is_far(app):
    app.ui.hero.set_mode('whip')
    app.ui.hero._clock_started -= 1
    from codex_whip.effects import CodexWhipEffects
    hero = app.ui.hero
    app.effects.preview_frame.return_value = (CodexWhipEffects.IDLE,(0.,0.))
    hero.clock_enabled = True
    hero._draw_live_whip()
    hero._set_clock(True)
    w,h = max(120,hero.winfo_width()),max(120,hero.winfo_height())
    hero._hover_motion(SimpleNamespace(x=w/2+30,y=h/2))
    assert hero._tilt_target[1] > 0
    hero._tilt_at -= 1
    hero._clock_started -= 1
    hero._draw_live_whip()
    assert hero._tilt[1] > 0
    hero._hover_motion(SimpleNamespace(x=w*2,y=h*2))
    assert not hero._clock_hover
    assert hero._tilt_target == (0,0)
    hero._clock_started -= 1
    hero._draw_live_whip()
    assert all(hero.itemcget(i,'state')=='hidden' for i in hero._dial_edges)


def test_visual_error_does_not_stop_whip_event_pump(app):
    import asyncio
    import threading
    from codex_whip.gui import GuiEventProcessor
    from codex_whip.models import WhipEvent
    app.effects.play.side_effect=RuntimeError('presentation failed')
    processor=GuiEventProcessor(Settings(),threading.Event(),app.emit)
    asyncio.run(processor.handle(WhipEvent(123,980.,3.2,120)))
    app.emit('log','event pump still alive')
    app._drain_events()
    text=app.log_text.get('1.0','end')
    assert '抽打画面异常' in text
    assert 'event pump still alive' in text
    assert '#123' in app.last_event_value.get()


def test_target_switch_disarms_and_hides_previous_overlay(app):
    app.armed.set()
    app.arm_value.set(True)
    generation = app._arm_generation
    assert app.select_target_app('Claude')
    assert app.settings.codex.target_app == 'Claude'
    assert not app.armed.is_set()
    assert not app.arm_value.get()
    assert app._arm_generation > generation
    app.effects.detach.assert_called()
    assert not app.select_target_app('Unknown')
    assert app.settings.codex.target_app == 'Claude'


def test_hover_clock_is_local_interruptible_and_yields_to_recording(app):
    app.ui.hero.set_mode('whip')
    app.ui.hero._clock_started -= 1
    from codex_whip.effects import CodexWhipEffects
    hero = app.ui.hero
    app.effects.preview_frame.return_value = (CodexWhipEffects.IDLE, (0.,0.))
    hero.clock_enabled = True
    hero._draw_live_whip()
    original = hero._display_pose
    hero._hover_motion(SimpleNamespace(x=original.handle_end[0],y=original.handle_end[1]))
    assert hero._clock_hover
    hero._clock_started -= 1
    hero._draw_live_whip()
    assert hero._display_pose.handle_end == (max(120,hero.winfo_width())/2,max(120,hero.winfo_height())/2)
    assert hero._clock_alpha == 1
    clock = hero._display_pose
    hero._set_clock(False)
    assert hero._clock_source == clock
    hero._clock_started -= 1
    hero._draw_live_whip()
    assert hero._display_pose == original
    hero._set_clock(True)
    hero.set_mode("recording")
    hero._draw()
    assert not hero._clock_hover
    assert hero._clock_alpha == 0
    assert all(hero.itemcget(i,"state") == "hidden" for i in hero._clock_items)


@pytest.fixture(scope="module")
def host():
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(host, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_WHIP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("codex_whip.gui.CodexWhipEffects", Mock())
    for name in ("start_listening", "check_codex", "_replace_scare_hotkey", "_schedule_effect_target_refresh"):
        monkeypatch.setattr(CodexWhipWindow, name, lambda *a, **kw: None)
    root = tk.Toplevel(host)
    result = CodexWhipWindow(root, Settings(), None)
    root.geometry("560x660+10000+10000")
    yield result
    if not result.closing:
        result.close()


def refresh(ui):
    if ui._timer:
        ui.root.after_cancel(ui._timer)
    ui._refresh()
    ui.root.update_idletasks()


def test_home_title_changes_with_clock_hover(app):
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    refresh(ui)
    assert ui.title.cget('text') == 'just beat it'


def test_power_setting_round_trip_and_disconnect_status(app):
    from codex_whip.models import DeviceMessage
    app._ensure_settings()
    app.ble_connected = True
    app.send_device_command = Mock(return_value=True)
    app.emit('device', DeviceMessage('PONG', ('0.6.1',), ''))
    app._drain_events()
    assert any(c.args == ('POWER,0',) for c in app.send_device_command.call_args_list)
    assert app.apply_power_settings(True)
    app.send_device_command.assert_called_with('POWER,1')
    app.emit('device',DeviceMessage('POWER',('1','SLEEP','300'),''))
    app._drain_events()
    assert '省电中' in app.settings_window.power_status.get()
    connected(app)
    app.ui.stage = 'ready'
    refresh(app.ui)
    assert app.ui.hero.mode == 'sleep'
    assert app.ui.title.cget('text').startswith('deep sleep.')
    app.emit('device',DeviceMessage('POWER',('1','ACTIVE','300'),''))
    app._drain_events()
    assert '已开启' in app.settings_window.power_status.get()
    refresh(app.ui)
    assert app.ui.hero.mode == 'whip'
    app.emit('ble','disconnected')
    app._drain_events()
    assert app.firmware_version == ''
    assert '连接手柄后同步' == app.settings_window.power_status.get()


def test_sleep_title_animates_only_the_ellipsis(app):
    assert [sleep_dot_count(value) for value in (0, .9, 1.8, 2.7, 3.6)] == [1, 2, 3, 2, 1]
    title = app.ui.title
    title.configure(text='deep sleep.')
    title._at -= 1
    title._frame()
    assert title._timer is None
    title.configure(text='deep sleep..')
    assert title.cget('text') == 'deep sleep..'
    assert title._timer is None


def test_disconnected_loader_returns_to_whip(app):
    ui = app.ui
    ui.stage = 'ready'
    refresh(ui)
    assert ui.hero.mode == 'connecting'
    assert not ui.connection_label.winfo_manager()
    assert not ui.hero.clock_enabled
    ui.hero._clock_started -= 1
    ui.hero._loading_transition_at -= 1
    ui.hero._draw_live_whip()
    ring = ui.hero._display_pose
    assert ui.hero._loading_alpha == 1
    connected(app)
    refresh(ui)
    assert ui.hero.mode == 'whip'
    assert ui.hero._clock_source == ring
    ui.hero._clock_started -= 1
    ui.hero._loading_transition_at -= 1
    ui.hero._draw_live_whip()
    assert ui.hero._loading_alpha == 0
    assert all(ui.hero.itemcget(i,'state') == 'hidden' for i in ui.hero._loading_dots)
    ui.hero._set_clock(True)
    refresh(ui)
    assert ui.title.cget('text') == "Don't waste time on AI"
    ui.hero._set_clock(False)
    refresh(ui)
    assert ui.title.cget('text') == 'just beat it'


def test_home_battery_indicator_tracks_device_and_disconnect(app):
    from codex_whip.models import DeviceMessage

    app.emit('device', DeviceMessage('BATTERY', ('74', '0'), ''))
    app._drain_events()
    assert app.ui.battery_indicator.percent == 74
    assert app.ui.battery_indicator.color == app.ui.battery_indicator.NORMAL
    assert app.ui.battery_indicator.find_withtag('glyph')
    assert app.ui.battery_indicator.find_withtag('percentage') == ()
    assert int(float(app.ui.battery_indicator.cget('width'))) == 32
    assert int(float(app.ui.battery_indicator.cget('height'))) == 18
    assert app.ui.battery_indicator.find_withtag('bolt') == ()

    app.emit('device', DeviceMessage('BATTERY', ('18', '0'), ''))
    app._drain_events()
    assert app.ui.battery_indicator.color == app.ui.battery_indicator.LOW

    app.emit('device', DeviceMessage('BATTERY', ('55', '1'), ''))
    app._drain_events()
    assert app.ui.battery_indicator.charging
    assert app.ui.battery_indicator.color == app.ui.battery_indicator.CHARGING
    assert app.ui.battery_indicator.find_withtag('glyph')
    assert app.ui.battery_indicator.find_withtag('percentage') == ()
    assert app.ui.battery_indicator.tooltip_text == '剩余电量 55%'

    app.emit('ble', 'disconnected')
    app._drain_events()
    assert app.ui.battery_indicator.percent is None
    assert app.ui.battery_indicator.find_withtag('glyph')


def connected(app):
    app.ble_connected = True
    app.worker_loop = Mock()
    app.firmware_supports_raw = True
    app.send_settings_command = lambda _: True
    app.ui.observe("sensor_pose", SensorPose(0, 0, 0, 0))


def test_three_settings_groups_preserve_controls_and_reopen(app):
    connected(app)
    ui = app.ui
    assert list(ui.nav) == ['general','calibration','input']
    app.voice_text.insert('1.0','未保存的语音文字')
    for section in ('general','input','calibration','general'):
        ui.open_preferences(section)
        app.root.update_idletasks()
        window = app.settings_window
        assert window.page_title.cget('text') == {'general':'通用','input':'输入','calibration':'手柄'}[section]
        assert bool(ui._general_body.winfo_manager()) == (section=='general')
        assert bool(ui._direction_card.winfo_manager()) == (section=='calibration')
        assert bool(ui._speech_panel.winfo_manager()) == (section=='input')
        assert bool(window.tap_advanced.winfo_manager()) == (section=='calibration')
        assert bool(window.voice_feature_card.winfo_manager()) == (section=='input')
        assert app.voice_text.get('1.0','end-1c') == '未保存的语音文字'
    ui.hide_preferences()
    ui.open_preferences('input')
    assert app.voice_text.get('1.0','end-1c') == '未保存的语音文字'
    assert app.settings_window._messages_panel.winfo_manager() == 'pack'


def test_generated_assets_have_real_alpha_and_content():
    for name in ("whip.png", "microphone.png"):
        with Image.open(asset_path(name)) as image:
            assert image.mode == "RGBA"
            assert image.getchannel("A").getextrema() == (0, 255)
            assert image.getbbox() is not None


def test_skip_setup_persists_without_fabricating_calibration(app):
    from codex_whip.interface_state import InterfacePreferences
    ui = app.ui
    ui.stage = 'connect'
    ui._learning_queue = ['whip','tap']
    app.armed.set()
    ui.skip_setup()
    assert ui.stage == 'ready'
    assert not app.armed.is_set()
    assert not ui._learning_queue
    assert InterfacePreferences.load(ui.path,already_calibrated=False).setup_complete
    assert not app.mounting_path.exists()
    refresh(ui)
    assert not ui.skip_setup_button.winfo_manager()
    ui.restart_setup()
    refresh(ui)
    assert ui.stage == 'connect'
    assert ui.skip_setup_button.winfo_manager() == 'pack'


def test_gear_hover_keeps_layout_and_reverses(app):
    gear = app.settings_button
    before = (gear.winfo_reqwidth(),gear.winfo_reqheight())
    gear._retarget(1.2)
    gear.after_cancel(gear._timer)
    gear._at -= .08
    gear._frame()
    assert 1 < gear._scale < 1.2
    shown = gear._scale
    gear._retarget(1.)
    assert gear._from == shown
    gear.after_cancel(gear._timer)
    gear._at -= 1
    gear._frame()
    assert gear._scale == 1
    assert (gear.winfo_reqwidth(),gear.winfo_reqheight()) == before


def test_title_chinese_glyphs_are_not_identical_missing_boxes(app):
    # Pillow does not inherit Tk's automatic CJK font fallback.
    assert app.ui.title._raster('录').tobytes() != app.ui.title._raster('音').tobytes()


def test_morphing_title_interrupts_and_fits_long_text(app):
    title = app.ui.title
    title.configure(text='just beat it')
    title.after_cancel(title._timer)
    title._at -= .14
    title._frame()
    shown = title._mask.copy()
    title.configure(text="Don't waste time on AI")
    assert title._old.tobytes() == shown.tobytes()
    title.after_cancel(title._timer)
    title._at -= 1
    title._frame()
    assert title._mask.tobytes() == title._new.tobytes()
    box = title._mask.getbbox()
    assert box and box[0]>0 and box[2]<880
    assert title._timer is None


def test_connection_must_have_fresh_sensor_data_before_calibration(app):
    ui = app.ui
    assert ui.stage == "connect"
    connected(app)
    refresh(ui)
    assert ui.stage == "calibrate"
    assert app.mount_window is None
    assert str(ui.primary["state"]) == "normal"
    ui._sensor_at = time.monotonic() - 2
    refresh(ui)
    assert str(ui.primary["state"]) == "disabled"
    assert not app.armed.is_set()


def test_skip_learning_preserves_all_existing_profiles_and_never_arms(app, tmp_path):
    profile = tmp_path / "detector-profile.json"
    profile.write_text("keep this byte-for-byte")
    save_mounting_profile(MountingProfile((0, 0, 1), 30, 30), app.mounting_path)
    before = app.mounting_path.read_bytes()
    app.ui.observe("mount_closed", {"saved": True})
    assert app.ui.stage == "choices"
    app.ui.skip_learning()
    refresh(app.ui)
    assert app.ui.stage == "ready"
    assert app.mounting_path.read_bytes() == before
    assert profile.read_text() == "keep this byte-for-byte"
    assert not app.armed.is_set()
    assert not app.voice_store.settings.enabled


def test_settings_sections_embed_all_existing_capabilities(app):
    for section in ("general", "messages", "voice", "detector", "visual"):
        app.ui.open_preferences(section)
        app.root.update_idletasks()
    window = app.settings_window
    assert window._embedded
    assert window.record_button.winfo_exists()
    assert window.voice_record_button.winfo_exists()
    assert window.visual_frequency_slider.winfo_exists()
    assert window._message_widgets
    app.ui.hide_preferences()
    assert app.settings_window is None
    assert app.log_text.winfo_exists()
    assert app.arm_check.winfo_exists()


def test_recording_pending_and_errors_render_without_changing_backend(app):
    connected(app)
    ui = app.ui
    ui.stage = "ready"
    ui.observe("voice_state", {"state": "recording"})
    ui.observe("ui_audio_level", 0.8)
    refresh(ui)
    assert ui.hero.mode == "recording"
    # A silent/stale stream is never turned into random artificial audio bars.
    ui.hero._audio_at = time.monotonic() - 1
    ui.hero._wave_at = 0
    ui.hero._draw()
    assert ui.hero._levels[-1] == 0
    ui.observe("voice_state", {"state": "recognizing"})
    refresh(ui)
    assert ui.title.cget("text") == "recognizing voice"
    ui.observe("voice_pending", "测试语音")
    ui.observe("voice_state", {"state": "ready"})
    refresh(ui)
    assert ui.pending.cget("text") == "测试语音"
    ui.observe("voice_pending", None)
    ui.observe("voice_state", {"state": "empty"})
    refresh(ui)
    assert ui.hero.mode == "whip"
    ui.observe("whip", {})
    assert time.monotonic() - ui.hero._strike_at < 0.1
    assert app.voice_module.pending_text is None
    assert not app.armed.is_set()


def test_empty_voice_feedback_expires_and_new_recording_wins(app, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("codex_whip.interface.time.monotonic", lambda: clock[0])
    connected(app)
    ui = app.ui
    ui.stage = "ready"
    ui.observe("voice_state", {"state": "empty"})
    refresh(ui)
    assert ui.title.cget("text") == "just beat it"
    assert ui.subtitle.cget("text") == ""
    clock[0] += 3.1
    refresh(ui)
    assert ui.subtitle.cget("text") == ""
    ui.observe("voice_state", {"state": "empty"})
    ui.observe("voice_state", {"state": "recording"})
    clock[0] += 4
    refresh(ui)
    assert ui.title.cget("text") == "recording"
    assert ui.hero.mode == "recording"


def test_voice_error_is_transient_without_dismissing_pending_text(app):
    ui = app.ui
    ui.stage = "ready"
    ui.observe("voice_error", "failure")
    refresh(ui)
    assert ui.subtitle.cget("text") == ""
    ui._notice_until = 0
    refresh(ui)
    assert "录音没完成" not in ui.subtitle.cget("text")
    ui.observe("voice_pending", "保留候选")
    ui.observe("voice_state", {"state": "ready"})
    refresh(ui)
    assert ui.pending.cget("text") == "保留候选"


def test_advanced_settings_collapsed_and_preserve_values(app):
    connected(app)
    app.ui.open_preferences("detector")
    window = app.settings_window
    sections = (window.detector_advanced, window.voice_advanced,
                window.tap_advanced, app.ui.diagnostics)
    original = {key: value.get() for key, value in window._variables.items()}
    original_voice = window._read_voice_settings()
    for section in sections:
        assert not section.expanded
        assert not section.body.winfo_manager()
        section.toggle.invoke()
        assert section.body.winfo_manager() == "pack"
        section.toggle.invoke()
        assert not section.body.winfo_manager()
    assert {key: value.get() for key, value in window._variables.items()} == original
    assert window._read_voice_settings() == original_voice
    assert not app.armed.is_set()


def test_onboarding_whip_learning_is_manual_and_skip_resumes_engine(app):
    connected(app)
    ui = app.ui
    ui.stage = "choices"
    ui.whip_choice.set(True)
    ui.advance()
    refresh(ui)
    assert ui.stage == "learning"
    assert app.settings_window._stage == "positive"
    assert not app.settings_window._positive_motion
    assert ui.primary.cget("text") == "录入刚刚动作"
    ui.skip_learning()
    assert ui.stage == "ready"
    assert app.settings_window is None


def test_reduced_motion_and_callbacks_close_cleanly(app):
    app.ui.reduce_motion.set(True)
    app.ui.set_reduced_motion()
    assert app.ui.hero.reduce_motion
    app.ui.hero.strike()
    app.close()
    assert app.ui.hero._closed
    assert app.ui.hero._timer is None


def test_tap_opt_in_opens_auto_calibration_without_manual_capture(app):
    connected(app)
    app.processor = Mock(_last_sensor_batch_at=time.monotonic())
    app.prepare_voice_model = Mock()  # No network in UI tests.
    ui = app.ui
    ui.stage = "choices"
    ui.tap_choice.set(True)
    ui.advance()
    assert app.voice_store.settings.enabled
    assert app.voice_module.calibration_active
    assert ui._tap_count == 0
    app.settings_window._record_voice_calibration = Mock(return_value=True)
    ui.advance()
    app.settings_window._record_voice_calibration.assert_not_called()
    assert ui._tap_count == 0
    refresh(ui)
    assert ui._advanced_section == 'calibration'
    assert app.settings_window.page_title.cget('text') == '手柄'
    ui.observe("tap_calibration_saved", app.voice_store.settings)
    refresh(ui)
    assert ui.primary.cget("text") == "完成并继续"
    ui.advance()
    assert ui.stage == "ready"
    assert not app.voice_module.calibration_active
    assert not app.armed.is_set()


def test_onboarding_rejects_late_arm_result_and_disabled_permission(app):
    app.ui.stage = "choices"
    app.arm_value.set(True)
    app.toggle_arm()
    assert not app.armed.is_set()
    app.emit("arm_result", (True, {"handle": 1, "pid": 1}, app._arm_generation))
    app._drain_events()
    assert not app.armed.is_set()


def test_advanced_small_window_has_scrollable_content(app):
    app.ui.open_preferences("detector")
    app.ui.settings.geometry("970x760+10000+10000")
    app.root.update_idletasks()
    canvas = app.ui._advanced_canvas
    bbox = canvas.bbox("all")
    assert bbox[3] >= canvas.winfo_height()
    canvas.yview_moveto(1)
    app.root.update_idletasks()
    assert app.settings_window.save_button.winfo_exists()


@pytest.mark.parametrize("section", ["general", "messages", "detector", "voice", "visual", "calibration"])
def test_redesigned_settings_buttons_fit_minimum_width(app, section):
    from codex_whip.settings_style import ActionButton
    connected(app)
    app.ui.open_preferences(section)
    app.ui.settings.geometry("970x760+10000+10000")
    app.root.update()
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    left = app.ui.settings.winfo_rootx()
    right = left + app.ui.settings.winfo_width()
    for widget in descendants(app.ui.settings):
        if isinstance(widget, ActionButton) and widget.winfo_viewable():
            assert widget.winfo_rootx() >= left, widget.cget("text")
            assert widget.winfo_rootx() + widget.winfo_width() <= right, widget.cget("text")
    if section != "general":
        titles = {"messages": "输入", "detector": "手柄", "voice": "输入", "visual": "通用", "calibration": "手柄"}
        assert app.settings_window.page_title.cget("text") == titles[section]


def test_native_styled_controls_keep_disabled_and_variable_semantics(host):
    from codex_whip.settings_style import ActionButton, Switch
    frame = tk.Frame(host, bg="white")
    called = []
    flag = tk.BooleanVar(master=host, value=False)
    switch = Switch(frame, text="开关", variable=flag, command=lambda: called.append(flag.get()))
    button = ActionButton(frame, "保存", lambda: called.append("save"), primary=True)
    try:
        switch.invoke()
        assert flag.get() is True and called == [True]
        switch.configure(state="disabled")
        switch.invoke()
        assert flag.get() is True and called == [True]
        button.configure(state="disabled")
        button.invoke()
        assert called == [True]
        button.configure(state="normal", text="保存并继续")
        button.invoke()
        assert called[-1] == "save"
        assert button.cget("text") == "保存并继续"
    finally:
        frame.destroy()


@pytest.mark.parametrize("stage", ["connect", "calibrate", "choices"])
def test_first_run_primary_is_inside_minimum_window(app, stage):
    app.root.geometry("500x620+10000+10000")
    if stage == "calibrate":
        connected(app)
    app.ui.stage = stage
    app.root.deiconify()
    app.root.update()
    refresh(app.ui)
    primary = app.ui.primary
    assert primary.winfo_ismapped()
    bottom = primary.winfo_rooty() + primary.winfo_height()
    assert bottom <= app.root.winfo_rooty() + app.root.winfo_height() - 16


def test_minimal_header_and_footer(app):
    refresh(app.ui)
    assert app.ui.connection_label.cget("text") == "●"
    assert app.ui.connection_label.master is app.settings_button.master
    assert not app.ui.footer.winfo_children()
    assert not hasattr(app.ui, "mode")
    header_text = [
        w.cget("text")
        for w in app.settings_button.master.winfo_children()
        if "text" in w.keys()
    ]
    assert "Codex 鞭子" not in header_text
    assert app.ui.battery_indicator.winfo_manager() == "pack"


def test_hero_reads_exact_overlay_pose_and_relative_motion(app):
    app.ui.hero.set_mode('whip')
    app.ui.hero._clock_started -= 1
    from codex_whip.effects import CodexWhipEffects
    pose = CodexWhipEffects.IDLE
    hero = app.ui.hero
    app.effects.preview_frame.return_value = (pose, (0.0, 0.0))
    app.root.update_idletasks()
    hero._draw_live_whip()
    drawing = hero._whip_drawing
    first = drawing.display_handle
    app.effects.preview_frame.return_value = (pose, (0.1, -0.1))
    hero._draw_live_whip()
    second = drawing.display_handle
    assert second[0] - first[0] == pytest.approx(max(120, hero.winfo_width()) * 0.07)
    assert second[1] - first[1] == pytest.approx(-max(120, hero.winfo_height()) * 0.055)
    app.effects.preview_frame.return_value = (CodexWhipEffects.STRIKE, (0.1, -0.1))
    hero._draw_live_whip()
    assert hero._preview_key[0] == CodexWhipEffects.STRIKE
    drawing.hide()
    hero._preview_key = None
    hero._draw_live_whip()
    assert drawing.visible


def test_shared_drawing_matches_source_pose_without_scale_accumulation(host):
    from codex_whip.whip_drawing import WhipDrawing
    from codex_whip.effects import CodexWhipEffects
    canvas = tk.Canvas(host)
    drawing = WhipDrawing(canvas)
    try:
        pose = CodexWhipEffects.IDLE
        drawing.draw(pose)
        assert canvas.coords(drawing._handle) == list((*pose.handle_start, *pose.handle_end))
        drawing.draw(pose, scale=0.5, offset=(20, 40))
        first = canvas.coords(drawing._handle)
        width = canvas.itemcget(drawing._handle, "width")
        drawing.draw(pose, scale=0.5, offset=(20, 40))
        assert canvas.coords(drawing._handle) == first
        assert canvas.itemcget(drawing._handle, "width") == width
        drawing.draw(pose)
        assert float(canvas.itemcget(drawing._handle, "width")) == 8
    finally:
        canvas.destroy()


def test_settings_layout_stacking_and_group_ownership(app):
    connected(app)
    ui = app.ui
    ui.open_preferences("general")
    app.root.update_idletasks()
    win = app.settings_window
    assert ui._general_body.winfo_reqheight() > 70
    assert win._content.pack_slaves().index(ui._general_body) < win._content.pack_slaves().index(win._visual_panel)
    siblings = list(ui.host.winfo_children())
    assert siblings.index(ui._general_body) > siblings.index(ui._advanced_panel)
    assert not hasattr(win, "visual_scare_hotkey_entry")
    app.effects.set_settings_open.assert_called_with(True)
    ui.open_preferences("input")
    app.root.update_idletasks()
    assert ui._sending_card.winfo_manager() == "pack"
    assert win.voice_feature_card.winfo_manager() == "pack"
    assert int(win._message_canvas.cget("height")) == max(80, win._message_list.winfo_reqheight())
    ui.open_preferences("calibration")
    assert win.tap_advanced.pack_info()["in"] == win._calibration_panel
    assert not win.record_button.winfo_manager()
    assert win.tap_auto_button.winfo_manager()
    ui.hide_preferences()
    app.effects.set_settings_open.assert_called_with(False)
