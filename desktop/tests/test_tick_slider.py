import tkinter as tk
from types import SimpleNamespace
import pytest
from codex_whip.tick_slider import TickSlider, render_track


@pytest.fixture(scope='module')
def root():
    root = tk.Tk()
    root.geometry('360x120+10000+10000')
    yield root
    root.destroy()


@pytest.fixture
def slider(root):
    seen = []
    widget = TickSlider(root, variable=tk.DoubleVar(root, value=.7), command=seen.append, bg='white')
    widget.pack(fill='x')
    root.update()
    yield widget, seen
    if widget.winfo_exists():
        widget.destroy()


def test_track_drag_is_continuous_and_clamps_without_delayed_feedback(slider):
    widget, seen = slider
    widget._press(SimpleNamespace(x=16))
    assert widget.get() == .2 and seen[-1] == .2
    widget._drag(SimpleNamespace(x=1000))
    assert widget.get() == 1 and seen[-1] == 1
    widget._drag(SimpleNamespace(x=-100))
    assert widget.get() == .2
    widget.set(.726)
    assert widget.get() == .726
    assert int(widget.cget('highlightthickness')) == 0
    widget._drag_offset = 0
    widget._drag(SimpleNamespace(x=101))
    first = widget.get()
    widget._drag(SimpleNamespace(x=102))
    assert 0 < widget.get()-first < .01


def test_thumb_grab_keeps_pointer_offset_and_keyboard_works(slider):
    widget, seen = slider
    x = 16+(widget.winfo_width()-32)*(.7-.2)/.8
    widget._press(SimpleNamespace(x=x+6))
    assert widget.get() == .7
    widget._key(.75)
    assert widget.get() == .75
    widget.configure(state='disabled')
    widget._key(1)
    widget._press(SimpleNamespace(x=16))
    assert widget.get() == .75


def test_trace_removed_on_destroy(slider):
    widget, _ = slider
    variable = widget.variable
    widget.destroy()
    assert variable.trace_info() == []


def test_render_matches_reference_colors_and_thumb():
    image = render_track(320, .25)
    assert image.getpixel((30,22)) == (25,25,27)
    assert image.getpixel((200,22)) == (231,231,233)
    assert image.getpixel((88,22)) == (255,255,255)


def test_thumb_press_animation_reverses_from_displayed_value(slider, monkeypatch):
    widget, _ = slider
    now = [10.]
    monkeypatch.setattr('codex_whip.tick_slider.time.monotonic',lambda: now[0])
    x = 16+(widget.winfo_width()-32)*(.7-.2)/.8
    widget._press(SimpleNamespace(x=x,y=22))
    assert widget._thumb_target == 1.45
    widget.after_cancel(widget._animation)
    now[0] += .08
    widget._animate_frame()
    assert 1 < widget._thumb_scale < 1.45
    displayed = widget._thumb_scale
    widget._release(SimpleNamespace(x=x,y=22))
    assert widget._thumb_from == displayed
    assert widget._thumb_target == 1
    widget.after_cancel(widget._animation)
    now[0] += .2
    widget._animate_frame()
    assert widget._thumb_scale == 1
    assert widget.get() == .7
