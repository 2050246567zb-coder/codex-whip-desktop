"""Presentation contracts; real BLE and sending are explicitly out of scope."""
import time
import pytest
from test_interface import host, app, connected, refresh


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
    hero._voice_at -= .14
    hero._draw()
    midway = hero._display_pose
    assert midway != source
    assert 0 < hero._voice_amount < 1
    ui.observe('voice_state', {'state': 'recognizing'})
    refresh(ui)
    assert hero._voice_source == midway
    hero._voice_at -= 1
    hero._draw()
    assert hero._voice_amount == 0
    assert hero._display_pose == source
    assert not hero.find_withtag('voice_art')
    app.effects.set_sensor_pose.assert_not_called()


def test_recording_centers_head_hides_rope_and_reduced_motion_stops_ripples(app):
    connected(app)
    ui = app.ui
    ui.stage = 'ready'
    ui.observe('voice_state', {'state': 'recording'})
    refresh(ui)
    hero = ui.hero
    hero._voice_at -= 1
    hero._draw()
    w,h = max(120,hero.winfo_width()),max(120,hero.winfo_height())
    pose = hero._display_pose
    assert pose.handle_end == (w/2,h/2)
    assert pose.handle_start[0] == w/2
    assert pose.handle_start[1] > pose.handle_end[1]
    assert hero._voice_amount == 1
    assert all(hero.itemcget(i,'fill') == hero.cget('bg').lower()
               for i in hero._whip_drawing._cord_segments)
    assert len(hero.find_withtag('voice_art')) == 4  # Three rings + grille.
    hero.reduce_motion = True
    hero._draw()
    assert len(hero.find_withtag('voice_art')) == 1


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
    assert ui.title.cget('text') == 'just beat it'
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
