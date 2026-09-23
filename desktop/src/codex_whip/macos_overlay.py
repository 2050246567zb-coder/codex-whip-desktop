"""Native transparent presentation for the existing overlay canvas scene.

Tk remains the scene model; its withdrawn backing windows never reach the screen.
AppKit owns compositing and clears the entire backing surface on every frame.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from functools import lru_cache
from collections import deque
import AppKit
import Foundation
import objc
import Quartz


@lru_cache(maxsize=32)
def _color(value):
    if not value:
        return None
    value = value.lstrip('#')
    if len(value) == 3:
        value = ''.join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f'Unsupported overlay color: {value}')
    return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
        int(value[:2], 16) / 255, int(value[2:4], 16) / 255,
        int(value[4:], 16) / 255, 1.0)


class WhipOverlayView(AppKit.NSView):
    def initWithFrame_(self, frame):
        self = objc.super(WhipOverlayView, self).initWithFrame_(frame)
        if self is not None:
            self.scene = []
            self.handlers = {}
            self._control_click = False
        return self

    def isOpaque(self):
        return False

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def dispatch(self, name, event):
        callback = self.handlers.get(name)
        if callback is not None:
            point = self.window().convertPointToScreen_(event.locationInWindow())
            screen = AppKit.NSScreen.screens()[0].frame()
            callback(SimpleNamespace(x_root=point.x, y_root=screen.origin.y + screen.size.height - point.y))

    def mouseDown_(self, event):
        self._control_click = bool(event.modifierFlags() & AppKit.NSEventModifierFlagControl)
        self.dispatch('right' if self._control_click else 'press', event)

    def mouseDragged_(self, event):
        if not self._control_click:
            self.dispatch('drag', event)

    def mouseUp_(self, event):
        if not self._control_click:
            self.dispatch('release', event)
        self._control_click = False

    def rightMouseDown_(self, event):
        self.dispatch('right', event)

    def mouseMoved_(self, event):
        self.dispatch('motion', event)

    def drawRect_(self, rect):
        # Copying transparent pixels is essential; source-over clear color
        # leaves previous pixels untouched and produces motion trails.
        Quartz.CGContextClearRect(AppKit.NSGraphicsContext.currentContext().CGContext(), self.bounds())
        for kind, coordinates, options in self.scene:
            if kind == 'text':
                font = AppKit.NSFont.fontWithName_size_(options['family'], options['size'])
                font = font or AppKit.NSFont.systemFontOfSize_(options['size'])
                text = Foundation.NSAttributedString.alloc().initWithString_attributes_(
                    options['text'], {AppKit.NSFontAttributeName: font,
                                      AppKit.NSForegroundColorAttributeName: _color(options['fill'])})
                size = text.size()
                x, y = coordinates
                anchor = options['anchor']
                if 'w' not in anchor:
                    x -= size.width if 'e' in anchor else size.width / 2
                if 'n' not in anchor:
                    y -= size.height if 's' in anchor else size.height / 2
                text.drawInRect_(AppKit.NSMakeRect(x, y, size.width, size.height))
                continue
            if kind == 'image':
                image = options['image']
                x, y = coordinates
                width, height = options.get('logical_size', image.size())
                anchor = options['anchor']
                if 'w' not in anchor:
                    x -= width if 'e' in anchor else width / 2
                if 'n' not in anchor:
                    y -= height if 's' in anchor else height / 2
                image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                    AppKit.NSMakeRect(x, y, width, height), AppKit.NSZeroRect,
                    AppKit.NSCompositingOperationSourceOver, 1.0, True, None)
                continue
            path = AppKit.NSBezierPath.bezierPath()
            if kind in ('line', 'polygon'):
                points = list(zip(coordinates[::2], coordinates[1::2]))
                if not points:
                    continue
                path.moveToPoint_(points[0])
                path.appendBezierPathWithPoints_count_(points[1:], len(points) - 1)
                if kind == 'polygon':
                    path.closePath()
            elif kind in ('oval', 'rectangle'):
                x1, y1, x2, y2 = coordinates
                bounds = AppKit.NSMakeRect(x1, y1, x2 - x1, y2 - y1)
                if kind == 'oval':
                    path.appendBezierPathWithOvalInRect_(bounds)
                else:
                    path.appendBezierPathWithRect_(bounds)
            else:
                continue
            fill = _color(options.get('fill'))
            if fill is not None and kind != 'line':
                fill.setFill()
                path.fill()
            stroke = fill if kind == 'line' else _color(options.get('outline'))
            if stroke is not None and options.get('width', 1) > 0:
                stroke.setStroke()
                path.setLineWidth_(options.get('width', 1))
                path.setLineCapStyle_(AppKit.NSRoundLineCapStyle)
                path.setLineJoinStyle_(AppKit.NSRoundLineJoinStyle)
                path.stroke()


class NativeCanvasOverlay:
    """Small window adapter used only after Tk has constructed its scene."""
    def __init__(self, root, backing, canvas):
        self._root, self._backing, self._canvas = root, backing, canvas
        self._title = str(backing.title())
        backing.withdraw()
        backing.title(self._title + ' (hidden scene)')
        self._geometry = None
        initial_geometry = backing.geometry()
        self._timer = None
        self._images = {}
        self._closed = False
        self.native = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 1, 1), AppKit.NSWindowStyleMaskBorderless,
            AppKit.NSBackingStoreBuffered, False)
        self.native.setReleasedWhenClosed_(False)
        self.native.setTitle_(self._title)
        self.native.setOpaque_(False)
        self.native.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.native.setHasShadow_(False)
        self.native.setIgnoresMouseEvents_(True)
        self.native.setLevel_(AppKit.NSStatusWindowLevel)
        self.native.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
            AppKit.NSWindowCollectionBehaviorStationary |
            AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = WhipOverlayView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 1, 1))
        self.native.setContentView_(self.view)
        self.geometry(initial_geometry)

    def title(self):
        return self._title

    def geometry(self, value=None):
        if value is None:
            return self._geometry
        if value == self._geometry:
            return
        match = re.fullmatch(r'(\d+)x(\d+)([+-]\d+)([+-]\d+)', value)
        if match is None:
            raise ValueError(f'Invalid overlay geometry: {value}')
        width, height, left, top = map(int, match.groups())
        self._geometry = value
        primary = AppKit.NSScreen.screens()[0].frame()
        primary_top = primary.origin.y + primary.size.height
        self.native.setFrame_display_(
            AppKit.NSMakeRect(left, primary_top - top - height, width, height), False)

    def _scene(self):
        result, used_images = list(getattr(self, 'drawing_scene', ())), set()
        canvas = self._canvas
        for item in canvas.find_all():
            if canvas.itemcget(item, 'state') == 'hidden':
                continue
            kind = canvas.type(item)
            coordinates = tuple(canvas.coords(item))
            if kind == 'image':
                name = canvas.itemcget(item, 'image')
                if not name:
                    continue
                used_images.add(name)
                if name not in self._images:
                    png = canvas.tk.call(name, 'data', '-format', 'png')
                    data = Foundation.NSData.dataWithBytes_length_(png, len(png))
                    self._images[name] = AppKit.NSImage.alloc().initWithData_(data)
                if self._images[name] is not None:
                    result.append((kind, coordinates, {'image': self._images[name],
                        'anchor': canvas.itemcget(item, 'anchor'),
                        'logical_size': getattr(canvas, '_native_image_sizes', {}).get(item, self._images[name].size())}))
            elif kind == 'text':
                import tkinter.font
                font = tkinter.font.Font(root=canvas, font=canvas.itemcget(item, 'font'))
                size = font.actual('size')
                if size > 0:
                    size *= float(canvas.tk.call('tk', 'scaling'))
                result.append((kind, coordinates, {
                    'text': canvas.itemcget(item, 'text'),
                    'fill': canvas.itemcget(item, 'fill') or '#000000',
                    'family': font.actual('family'), 'size': abs(size),
                    'anchor': canvas.itemcget(item, 'anchor')}))
            elif kind in ('line', 'oval', 'polygon', 'rectangle'):
                options = {'fill': canvas.itemcget(item, 'fill'),
                           'width': float(canvas.itemcget(item, 'width'))}
                if kind != 'line':
                    options['outline'] = canvas.itemcget(item, 'outline')
                result.append((kind, coordinates, options))
            else:
                raise ValueError(f'Unsupported native overlay canvas item: {kind}')
        self._images = {key: value for key, value in self._images.items() if key in used_images}
        return result

    def _render(self):
        self._timer = None
        if self._closed or not self.winfo_viewable():
            return
        try:
            self.view.scene = self._scene()
            self.view.setNeedsDisplay_(True)
            self.native.displayIfNeeded()
        except Exception:
            self.withdraw()
            raise
        self._timer = self._root.after(16, self._render)

    def deiconify(self):
        self.view.scene = self._scene()
        self.view.setNeedsDisplay_(True)
        self.native.orderFrontRegardless()
        if self._timer is None:
            self._render()

    def withdraw(self):
        if self._timer is not None:
            self._root.after_cancel(self._timer)
            self._timer = None
        self.native.orderOut_(None)

    def winfo_viewable(self):
        return bool(self.native.isVisible())

    def state(self):
        return 'normal' if self.winfo_viewable() else 'withdrawn'

    def lift(self):
        if self.winfo_viewable():
            self.native.orderFrontRegardless()

    def attributes(self, name, value=None):
        if name == '-transparent':
            return True
        if name == '-alpha':
            if value is not None:
                self.native.setAlphaValue_(float(value))
            return float(self.native.alphaValue())
        if name == '-topmost':
            return True
        raise ValueError(f'Unsupported native overlay attribute: {name}')

    def destroy(self):
        self.withdraw()
        self._closed = True
        self.native.close()
        self._backing.destroy()


class WhipInputPanel(AppKit.NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class NativeInputOverlay(NativeCanvasOverlay):
    """Nonactivating mouse target; no nearly-invisible Tk hit-test window."""
    def __init__(self, root, backing, handlers):
        self._root, self._backing = root, backing
        self._title = str(backing.title())
        backing.withdraw()
        backing.title(self._title + ' (hidden input)')
        self._geometry = None
        self._timer = None
        self._closed = False
        self.native = WhipInputPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, 1, 1),
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered, False)
        self.native.setReleasedWhenClosed_(False)
        self.native.setTitle_(self._title)
        self.native.setOpaque_(False)
        # Window alpha stays 1: macOS excludes windows with very low window
        # alpha from mouse targeting. This almost-clear fill is composited once.
        self.native.setBackgroundColor_(AppKit.NSColor.colorWithWhite_alpha_(0, 0.01))
        self.native.setHasShadow_(False)
        self.native.setIgnoresMouseEvents_(False)
        self.native.setHidesOnDeactivate_(False)
        self.native.setLevel_(AppKit.NSStatusWindowLevel)
        self.native.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces |
                                          AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.view = WhipOverlayView.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 1, 1))
        self._handlers = handlers
        self._events = deque()
        # Native mouse callbacks run inside Tk's Cocoa event dispatch. Calling
        # Tcl from there can deadlock. Only enqueue; drain in a Tk timer.
        self.view.handlers = {name: lambda event, name=name: self._events.append((name, event))
                              for name in handlers}
        self._event_timer = root.after(8, self._drain_events)
        self.native.setContentView_(self.view)
        self.geometry(backing.geometry())

    def deiconify(self):
        self.native.orderFrontRegardless()

    def _drain_events(self):
        self._event_timer = None
        if self._closed:
            return
        try:
            for _ in range(min(64, len(self._events))):
                if not self._events:
                    break
                name, event = self._events.popleft()
                self._handlers[name](event)
        finally:
            if not self._closed:
                self._event_timer = self._root.after(8, self._drain_events)

    def withdraw(self):
        self._events.clear()
        super().withdraw()

    def destroy(self):
        if self._event_timer is not None:
            self._root.after_cancel(self._event_timer)
            self._event_timer = None
        super().destroy()

    def grab_set(self):
        pass  # AppKit keeps the drag sequence with the mouse-down view.

    def grab_release(self):
        pass


class NativeWhipDrawing:
    """Build native line commands directly, avoiding per-frame Tcl round trips."""
    def __init__(self, overlay):
        self.overlay = overlay

    def draw(self, pose):
        import math
        from .effects import catmull_rom_points
        points = catmull_rom_points(pose.cord, 18)
        scene = []
        for color, width_offset, factor in (('#000103', 2.5, 1), ('#090B0E', 0, 1), ('#30343A', 0, .24)):
            for i in range(5):
                start = max(0, round(i * (len(points)-1) / 5) - (2 if i else 0))
                end = min(len(points)-1, round((i+1) * (len(points)-1) / 5) + (2 if i < 4 else 0))
                body = 7 - i / 4 * 4.5 + (2 if i == 0 else 0)
                coords = tuple(v for p in points[start:end+1] for v in p)
                scene.append(('line', coords, {'fill': color, 'width': max(1, (body+width_offset)*factor)}))
        for color, width in (('#000103',12),('#0A0C10',8)):
            scene.append(('line', (*pose.handle_start,*pose.handle_end), {'fill':color,'width':width}))
        x1,y1=pose.handle_start; x2,y2=pose.handle_end
        length=math.hypot(x2-x1,y2-y1) or 1
        nx,ny=-(y2-y1)/length,(x2-x1)/length
        scene.append(('line',(x1+nx*1.8,y1+ny*1.8,x2+nx*1.8,y2+ny*1.8),{'fill':'#343840','width':2}))
        self.overlay.drawing_scene = scene

    def hide(self):
        self.overlay.drawing_scene = []
