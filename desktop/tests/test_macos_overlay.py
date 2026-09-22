import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'darwin', reason='native AppKit renderer')


@pytest.fixture(scope='module')
def tk_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_native_frames_clear_previous_pixels_and_leave_background_transparent(tmp_path, tk_root):
    import AppKit as A
    import Quartz
    from PIL import Image
    from codex_whip.macos_overlay import WhipOverlayView
    view = WhipOverlayView.alloc().initWithFrame_(A.NSMakeRect(0, 0, 128, 128))
    bitmap = A.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, 128, 128, 8, 4, True, False, A.NSDeviceRGBColorSpace, 0, 0)
    context = A.NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap)
    A.NSGraphicsContext.saveGraphicsState()
    try:
        A.NSGraphicsContext.setCurrentContext_(context)
        def frame(x):
            view.scene = [('line', (x, 20, x, 108), {'fill': '#090B0E', 'width': 8})]
            view.drawRect_(view.bounds())
            Quartz.CGContextFlush(context.CGContext())
            return Image.frombytes('RGBA', (128, 128), bytes(bitmap.bitmapData()),
                                   'raw', 'RGBA', bitmap.bytesPerRow())
        first = frame(20)
        second = frame(100)
        assert first.getpixel((20, 64))[3] == 255
        assert first.getpixel((64, 64))[3] == 0
        assert second.getpixel((20, 64))[3] == 0, 'old stroke must be erased'
        assert second.getpixel((100, 64))[3] == 255
        assert second.getpixel((64, 64))[3] == 0
        first.save(tmp_path / 'native-first.png')
        second.save(tmp_path / 'native-second.png')
    finally:
        A.NSGraphicsContext.restoreGraphicsState()


def test_native_overlay_keeps_tk_backing_hidden_and_tracks_window_lifecycle(tk_root):
    import tkinter as tk
    from codex_whip.macos_overlay import NativeCanvasOverlay
    root = tk_root
    window = tk.Toplevel(root)
    window.withdraw()
    window.title('CodexWhip native overlay test')
    window.geometry('128x128+30+30')
    canvas = tk.Canvas(window)
    canvas.pack()
    canvas.create_line(20, 20, 80, 80, fill='#111111', width=5)
    window.update_idletasks()
    overlay = NativeCanvasOverlay(root, window, canvas)
    try:
        overlay.deiconify()
        root.update()
        assert overlay.winfo_viewable()
        assert window.state() == 'withdrawn'
        assert not overlay.native.isOpaque()
        assert overlay.native.backgroundColor().alphaComponent() == 0
        overlay.geometry('128x128+60+60')
        assert overlay.native.frame().origin.x == 60
        overlay.withdraw()
        assert not overlay.winfo_viewable()
        assert overlay._timer is None
    finally:
        overlay.destroy()


def test_native_scene_keeps_damage_image_alpha(tk_root):
    import tkinter as tk
    from PIL import Image, ImageTk
    from codex_whip.macos_overlay import NativeCanvasOverlay
    window = tk.Toplevel(tk_root)
    window.withdraw()
    window.title('CodexWhip image test')
    canvas = tk.Canvas(window)
    canvas.pack()
    pixels = Image.new('RGBA', (16, 16), (0, 0, 0, 0))
    pixels.putpixel((8, 8), (255, 0, 0, 255))
    photo = ImageTk.PhotoImage(pixels, master=canvas)
    canvas.create_image(0, 0, image=photo, anchor='nw')
    window.update_idletasks()
    overlay = NativeCanvasOverlay(tk_root, window, canvas)
    try:
        scene = overlay._scene()
        assert len(scene) == 1
        image = scene[0][2]['image']
        bitmap = image.representations()[0]
        assert bitmap.colorAtX_y_(0, 0).alphaComponent() == 0
        assert bitmap.colorAtX_y_(8, 8).alphaComponent() == 1
        canvas.delete('all')
        assert overlay._scene() == []
        assert overlay._images == {}
    finally:
        overlay.destroy()


def test_native_input_routes_click_drag_and_release_in_screen_coordinates(tk_root):
    import tkinter as tk
    import AppKit as A
    from codex_whip.macos_overlay import NativeInputOverlay
    events = []
    window = tk.Toplevel(tk_root); window.withdraw(); window.title('Input routing test')
    window.geometry('80x80+100+200'); window.update_idletasks()
    overlay = NativeInputOverlay(tk_root, window, {
        name: lambda event, name=name: events.append((name, event.x_root, event.y_root))
        for name in ('press', 'drag', 'release', 'right')})
    try:
        overlay.geometry('80x80+100+200')
        overlay.deiconify()
        assert not overlay.native.ignoresMouseEvents()
        assert overlay.native.alphaValue() == 1
        assert window.state() == 'withdrawn'
        def event(kind, point, modifiers=0):
            return A.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                kind, point, modifiers, 1, overlay.native.windowNumber(), None, 1, 1, 1)
        overlay.view.mouseDown_(event(A.NSEventTypeLeftMouseDown, (10, 70)))
        overlay.view.mouseDragged_(event(A.NSEventTypeLeftMouseDragged, (20, 60)))
        overlay.view.mouseUp_(event(A.NSEventTypeLeftMouseUp, (20, 60)))
        overlay.view.rightMouseDown_(event(A.NSEventTypeRightMouseDown, (20, 60)))
        assert events == [], 'native callbacks must not re-enter Tcl'
        import time
        deadline = time.monotonic() + .3
        while len(events) < 4 and time.monotonic() < deadline:
            tk_root.update()
        assert events == [('press', 110, 210), ('drag', 120, 220),
                          ('release', 120, 220), ('right', 120, 220)]
        events.clear()
        overlay.view.mouseDown_(event(A.NSEventTypeLeftMouseDown, (20, 60), A.NSEventModifierFlagControl))
        overlay.view.mouseUp_(event(A.NSEventTypeLeftMouseUp, (20, 60)))
        deadline = time.monotonic() + .3
        while not events and time.monotonic() < deadline:
            tk_root.update()
        assert events == [('right', 120, 220)], 'Control-click must not also arm manual whipping'
    finally:
        overlay.destroy()


def test_native_presentation_supports_clock_and_home_animation_states(tk_root):
    import tkinter as tk
    from types import SimpleNamespace
    from codex_whip.effects import CodexWhipEffects
    from codex_whip.macos_overlay import NativeCanvasOverlay, NativeWhipDrawing
    from codex_whip.overlay_presentation import OverlayPresentation
    window = tk.Toplevel(tk_root)
    window.withdraw()
    window.title('Native presentation regression')
    window.geometry('900x900+20+20')
    canvas = tk.Canvas(window, width=900, height=900)
    canvas.pack()
    window.update_idletasks()
    overlay = NativeCanvasOverlay(tk_root, window, canvas)
    effects = SimpleNamespace(window=overlay, canvas=canvas, IDLE=CodexWhipEffects.IDLE,
                              _preview_pose=CodexWhipEffects.IDLE, _visual_origin=(0, 0))
    presentation = OverlayPresentation(effects)
    drawing = NativeWhipDrawing(overlay)
    try:
        for mode in ('whip', 'connecting', 'recording', 'recognizing', 'sleep', 'whip'):
            presentation.update(mode=mode, title=mode, subtitle='', deadline=None,
                                clock_enabled=True, reduce_motion=True, level=.5)
            tk_root.update_idletasks()
            if mode == 'whip':
                presentation.render()
                presentation.toggle_clock()
                assert presentation.hero._clock_hover
            drawing.hide()
            presentation.render()
            overlay.deiconify()
            overlay._render()
            assert overlay.winfo_viewable()
            assert overlay.view.scene
            if mode == 'whip':
                assert any(kind == 'text' and opts['text'] == '12'
                           for kind, _, opts in overlay.view.scene)
            else:
                assert presentation.hero.mode == mode
            overlay.withdraw()
    finally:
        presentation.close()
        overlay.destroy()
