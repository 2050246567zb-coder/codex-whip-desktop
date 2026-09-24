"""Presentation contracts; real BLE and sending are explicitly out of scope."""
import time
import pytest
from test_interface import host, app, connected, refresh, advance_motion, ready_to_advance


def test_recording_morph_reverses_from_current_pose_and_keeps_overlay_read_only(app):
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    refresh(ui)
    hero = ui.hero
    hero._draw()
    source = hero._display_pose
    ui.observe('voice_state', {'state': 'recording'})
    refresh(ui)
    hero._draw()
    advance_motion(hero, 'voice', .5)
    hero._draw()
    midway = hero._display_pose
    assert midway != source
    assert 0 < hero._voice_amount < 1
    ui.observe('voice_state', {'state': 'recognizing'})
    assert hero.mode == 'recording'
    ready_to_advance(ui)
    recorded = hero._display_pose
    refresh(ui)
    assert hero._voice_source == recorded
    advance_motion(hero, 'voice')
    hero._draw()
    assert hero._voice_amount == 0
    assert hero._display_pose != source  # Recognition now flows around an open infinity.
    assert not hero.find_withtag('voice_art')
    app.effects.set_sensor_pose.assert_not_called()


def test_recognizing_flows_and_exits_from_current_pose(app):
    connected(app)
    hero = app.ui.hero
    hero.set_mode('recognizing')
    advance_motion(hero)
    hero._draw()
    first = hero._display_pose
    hero._recognizing_at -= .3
    hero._draw()
    assert hero._display_pose != first
    displayed = hero._display_pose
    hero.set_mode('whip')
    assert hero._clock_source == displayed
    advance_motion(hero)
    hero._draw()
    assert hero._clock_source is None
    app.effects.set_sensor_pose.assert_not_called()


def test_recognizing_reduced_motion_is_static(app):
    connected(app)
    hero = app.ui.hero
    hero.reduce_motion = True
    hero.set_mode('recognizing')
    hero._draw()
    first = hero._display_pose
    hero._recognizing_at -= .7
    hero._draw()
    assert hero._display_pose == first


@pytest.mark.parametrize('state, expected_mode, expected_title', [
    ('recording', 'recording', 'recording'),
    ('recognizing', 'recognizing', 'recognizing voice'),
])
def test_voice_animation_is_not_replaced_by_connecting_loader(
    app, state, expected_mode, expected_title
):
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    ui.observe('voice_state', {'state': state})
    app.ble_connected = False
    refresh(ui)
    assert ui.hero.mode == expected_mode
    assert ui.title.cget('text') == expected_title


def test_mic_head_grows_solid_and_error_returns_directly_to_whip(app, monkeypatch):
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    refresh(ui)
    hero = ui.hero
    hero._draw()
    ui.observe('voice_state', {'state':'recording'})
    refresh(ui)
    hero._draw()
    advance_motion(hero, 'voice', .5)
    hero._draw()
    head = hero.find_withtag('voice_art')[-1]
    assert hero.itemcget(head,'fill') == '#171717'
    partial_width = float(hero.itemcget(head,'width'))
    advance_motion(hero, 'voice')
    hero._draw()
    assert float(hero.itemcget(hero.find_withtag('voice_art')[-1],'width')) > partial_width
    def forbidden(*args, **kwargs):
        raise AssertionError('Error return must not use infinity geometry')
    monkeypatch.setattr('codex_whip.interface.recognizing_pose', forbidden)
    ui.observe('voice_error','录音过短')
    ready_to_advance(ui)
    refresh(ui)
    hero._draw()
    advance_motion(hero, 'voice', .5)
    hero._draw()
    assert hero.mode == 'whip'
    advance_motion(hero, 'voice')
    hero._draw()
    assert hero._voice_amount == 0
    assert not hero.find_withtag('voice_art')


def test_interrupt_mic_exit_uses_current_pose(app):
    connected(app)
    hero = app.ui.hero
    hero.set_mode('recording')
    hero._draw()
    advance_motion(hero, 'voice')
    hero._draw()
    hero.set_mode('recognizing')
    hero._draw()
    advance_motion(hero, 'voice', .35)
    hero._draw()
    displayed = hero._display_pose
    hero.set_mode('whip')
    assert hero._voice_source == displayed


def test_recording_centers_head_hides_rope_and_reduced_motion_stops_ripples(app):
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    ui.observe('voice_state', {'state': 'recording'})
    refresh(ui)
    hero = ui.hero
    assert hero.mode == 'recording'
    hero._draw()
    advance_motion(hero, 'voice')
    hero._draw()
    w,h = max(120,hero.winfo_width()),max(120,hero.winfo_height())
    pose = hero._display_pose
    assert pose.handle_end == (w/2,h/2)
    assert pose.handle_start[0] == w/2
    assert pose.handle_start[1] > pose.handle_end[1]
    assert hero._voice_amount == 1
    assert hero._whip_drawing.cord_opacity == 0
    assert len(hero.find_withtag('voice_art')) == 4  # Three rings + grille.
    hero.reduce_motion = True
    hero._draw()
    assert len(hero.find_withtag('voice_art')) == 1


def test_mic_transition_still_runs_if_double_tap_precedes_first_idle_frame(app):
    connected(app)
    hero = app.ui.hero
    hero._display_pose = None
    hero.set_mode('recording')
    hero._draw_live_whip()
    assert hero._voice_source is not None
    assert hero.transition_active
    advance_motion(hero, 'voice', .5)
    hero._draw_live_whip()
    assert 0 < hero._voice_amount < 1
    advance_motion(hero, 'voice')
    advance_motion(hero, 'clock')
    advance_motion(hero, 'loading')
    hero._draw_live_whip()
    assert hero._voice_amount == 1
    assert hero.transition_complete


def test_fast_result_shows_recognizing_state_after_mic_completes(app):
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    refresh(ui)
    ui.observe('voice_state', {'state': 'recording'})
    refresh(ui)
    assert ui.hero.mode == 'recording'
    ui.observe('voice_state', {'state': 'recognizing'})
    ui.observe('voice_pending', '测试文字')
    ui.observe('voice_state', {'state': 'ready'})
    assert ui.hero.mode == 'recording'
    ready_to_advance(ui)
    refresh(ui)
    assert ui.hero.mode == 'recognizing'
    assert ui.title.cget('text') == 'recognizing voice'
    ready_to_advance(ui)
    refresh(ui)
    assert ui.hero.mode == 'whip'
    assert ui.title.cget('text') == '测试文字'


def test_pending_title_wins_over_clock_and_whip_does_not_clear_unsent_text(app):
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    ui.observe('voice_pending', '把刚刚的问题修好')
    ui.observe('voice_state', {'state':'ready'})
    refresh(ui)
    assert ui.title.cget('text') == '把刚刚的问题修好'
    assert ui.subtitle.cget('text') == 'beat it, then send'
    assert ui.hero.mode == 'whip'
    assert not ui.hero.clock_enabled
    ui._clock_title_changed(True)
    ui.observe('whip', {})
    ui.observe('send_error','failure')
    refresh(ui)
    assert ui.title.cget('text') == '把刚刚的问题修好'
    ui.observe('voice_pending', '')  # Backend emits this only on clear/success.
    refresh(ui)
    assert ui.title.cget('text') == ''
    assert ui.subtitle.cget('text') == ''
    assert ui.title._timer is not None
    assert ui.subtitle._timer is not None


def test_long_transcription_is_wrapped_inside_title(app):
    title = app.ui.title
    text = '请继续完成这个项目的界面功能并检查所有动画是否可以正常显示。' * 8
    mask = title._raster(text)
    left, top, right, bottom = mask.getbbox()
    assert 0 <= left < right <= mask.width
    assert 0 <= top < bottom <= mask.height


def test_expired_voice_restores_normal_home_and_replacement_restarts_timer(app,monkeypatch):
    clock = [100.]
    monkeypatch.setattr('codex_whip.interface.time.monotonic',lambda:clock[0])
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    app.voice_module.set_pending('first')
    ui.observe('voice_pending','first')
    ui.observe('voice_state',{'state':'ready'})
    refresh(ui)
    assert ui.subtitle._deadline == 110.
    clock[0] = 109.
    app.voice_module.set_pending('second')
    ui.observe('voice_pending','second')
    refresh(ui)
    assert ui.subtitle._deadline == 119.
    clock[0] = 110.
    refresh(ui)
    assert ui.title.cget('text') == 'second'
    clock[0] = 119.
    refresh(ui)
    assert ui.title.cget('text') == ''
    assert ui.subtitle._deadline is None
    assert ui.subtitle._sand_timer is None
    assert app.voice_module.pending_text is None
