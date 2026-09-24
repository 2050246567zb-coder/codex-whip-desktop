import tkinter as tk
from types import SimpleNamespace
import pytest
from codex_whip.effects import CodexWhipEffects, WhipPose
from codex_whip.overlay_presentation import OverlayPresentation
from test_interface import host


@pytest.fixture
def overlay(host):
    root = tk.Toplevel(host)
    root.withdraw()
    canvas = tk.Canvas(root,width=1200,height=1200,bg='#ff00ff')
    effects = SimpleNamespace(window=root,canvas=canvas,IDLE=CodexWhipEffects.IDLE,
                              _preview_pose=CodexWhipEffects.IDLE,_visual_origin=(0,0))
    p = OverlayPresentation(effects)
    root.update_idletasks()
    yield p
    p.close()
    root.destroy()


def update(p,mode='whip',title='',subtitle='',deadline=None):
    p.update(mode=mode,title=title,subtitle=subtitle,deadline=deadline,
             clock_enabled=mode=='whip' and not subtitle,reduce_motion=False,level=.2)


def test_overlay_geometry_preserves_live_pose_and_text_follows(overlay):
    p = overlay
    update(p, title='测试文字', subtitle='beat it, then send')
    p.render()
    assert p.hit_pose == p.effects._preview_pose
    before = p.canvas.coords(p.text_items[0])
    pose = p.effects._preview_pose
    def move(pt): return pt[0]+60,pt[1]+35
    p.effects._preview_pose = WhipPose(move(pose.handle_start),move(pose.handle_end),tuple(map(move,pose.cord)))
    p.render()
    after = p.canvas.coords(p.text_items[0])
    assert after == pytest.approx([before[0]+60,before[1]+35])


def test_overlay_clock_is_right_click_only_and_voice_takes_priority(overlay):
    p = overlay
    update(p,title="Don't waste time on AI")
    assert not p.hero._clock_hover
    assert not p.hero.bind('<Motion>')
    assert p.title.cget('text') == ''
    p.render()
    p.toggle_clock()
    assert p.hero._clock_hover
    assert p.title.cget('text') == "Don't waste time on AI"
    p.hero._clock_started -= 1
    p.render()
    update(p,'recording','recording')
    assert not p.hero._clock_hover
    p.hero._voice_at -= 1
    p.render()
    assert p.hero._voice_amount == 1
    p.toggle_clock()
    assert not p.hero._clock_hover
    update(p,'recognizing','recognizing voice')
    p.hero._voice_at -= 1
    p.hero._clock_started -= 1
    p.render()
    assert p.hero.mode == 'recognizing'
    update(p,title='测试文字',subtitle='beat it, then send',deadline=123.)
    p.render()
    assert p.subtitle._deadline == 123.


def test_overlay_render_item_pool_does_not_leak(overlay):
    p=overlay
    update(p,'recording','recording')
    p.hero._voice_at -= 1
    for _ in range(30): p.render()
    count=len(p.canvas.find_all())
    for _ in range(30): p.render()
    assert len(p.canvas.find_all()) == count


@pytest.mark.parametrize('mode', ['whip','recording','recognizing','connecting','sleep'])
def test_strike_bypasses_status_geometry_and_uses_original_drawing(overlay,mode):
    from unittest.mock import Mock
    p=overlay
    update(p,mode)
    p.render()
    assert p.hero._timer is None
    p.begin_strike()
    assert not p.owns_geometry
    effect=object.__new__(CodexWhipEffects)
    effect._presentation=p
    effect._whip_drawing=Mock()
    effect._draw_pose(CodexWhipEffects.STRIKE)
    effect._whip_drawing.draw.assert_called_once_with(CodexWhipEffects.STRIKE)
    p.effects._preview_pose=CodexWhipEffects.STRIKE
    p.render()
    assert p.hit_pose == CodexWhipEffects.STRIKE


def test_failed_presentation_keeps_original_whip(overlay):
    from unittest.mock import Mock
    p=overlay
    effect=object.__new__(CodexWhipEffects)
    effect._preview_pose=CodexWhipEffects.IDLE
    effect._presentation=Mock()
    effect._presentation.begin_strike.side_effect=RuntimeError('renderer failed')
    effect._whip_drawing=Mock()
    effect._log=Mock()
    effect._begin_presentation_strike()
    assert effect._presentation is None
    assert effect._presentation_failed
    effect._draw_pose(CodexWhipEffects.STRIKE)
    effect._whip_drawing.draw.assert_called_with(CodexWhipEffects.STRIKE)


def test_ordinary_text_hidden_but_clock_and_voice_text_remain(overlay):
    p = overlay
    update(p)
    p.render()
    assert all(p.canvas.itemcget(i, 'state') == 'hidden' for i in p.text_items)
    p.toggle_clock()
    p.render()
    assert all(p.canvas.itemcget(i, 'state') == 'normal' for i in p.text_items)
    update(p, 'recognizing', 'recognizing voice')
    p.render()
    assert p.canvas.itemcget(p.text_items[0], 'state') == 'normal'


def test_mac_text_keeps_retina_pixels_and_partial_alpha(overlay):
    import sys
    from PIL import ImageTk
    if sys.platform != 'darwin':
        pytest.skip('AppKit alpha compositing')
    p = overlay
    update(p, 'recording', 'recording')
    p.title._mask = p.title._raster('Recording 录音')
    p.render()
    image = ImageTk.getimage(p.photos[0])
    logical = p.canvas._native_image_sizes[p.text_items[0]]
    assert image.width >= logical[0] * 2
    assert any(0 < a < 255 and count for count, a in image.getchannel('A').getcolors())
