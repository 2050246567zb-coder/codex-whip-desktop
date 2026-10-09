"""Route wheel and Tk 9 touchpad events throughout a settings viewport."""
from __future__ import annotations

import tkinter as tk


class SettingsScroll:
    def __init__(self, window: tk.Misc, canvas: tk.Canvas):
        self.window = window
        self.canvas = canvas
        self.tag = f"CodexWhipScroll{canvas}"
        window_system = window.tk.call("tk", "windowingsystem")
        # Aqua used +/-1 per wheel notch through Tk 8.6. Tk 8.7/9 uses
        # +/-120, like Windows/X11; applying the old Mac multiplier jumps
        # thousands of pixels on a single ordinary mouse-wheel event.
        legacy_aqua = window_system == "aqua" and int(window.tk.call(
            "package", "vcompare", window.tk.call("package", "provide", "Tk"), "8.7a0"
        )) < 0
        self._wheel_delta_unit = 1 if legacy_aqua else 120
        canvas.configure(yscrollincrement=1)
        window.bind_class(self.tag, "<MouseWheel>", self._wheel)
        try:
            window.bind_class(self.tag, "<TouchpadScroll>", self._touchpad)
        except tk.TclError:
            pass  # Tk 8.6 delivers touchpad gestures as MouseWheel.
        if window_system == "x11":
            window.bind_class(self.tag, "<Button-4>", lambda e: self._scroll(e, -40))
            window.bind_class(self.tag, "<Button-5>", lambda e: self._scroll(e, 40))
        # Rows created after opening settings receive the same routing tag.
        window.bind("<Map>", lambda e: self.install(e.widget), add="+")
        self.install(window)

    def install(self, widget: tk.Misc) -> None:
        tags = widget.bindtags()
        if self.tag not in tags:
            widget.bindtags((self.tag, *tags))
        for child in widget.winfo_children():
            self.install(child)

    def _wheel(self, event):
        delta = event.delta
        if not delta:
            return None
        pixels = -delta * 40 / self._wheel_delta_unit
        return self._scroll(event, pixels)

    def _touchpad(self, event):
        _dx, dy = self.window.tk.call("tk::PreciseScrollDeltas", event.delta)
        pixels = self.window.tk.call("tk::ScaleNum", -dy)
        return self._scroll(event, float(pixels))

    def _scroll(self, event, pixels: float):
        if not pixels or not self.canvas.winfo_ismapped() or event.state & 0x0005:
            return None  # Shift/Control gestures retain their own meaning.
        widget = event.widget
        if widget.winfo_class() == "Text":
            first, last = widget.yview()
            if (pixels < 0 and first > 0) or (pixels > 0 and last < 1):
                # Long text editors scroll internally; at their boundary the
                # gesture continues through the containing settings page.
                return None
        region = self.canvas.bbox("all")
        if region is None or region[3] - region[1] <= self.canvas.winfo_height():
            return None
        self.canvas.yview_moveto((self.canvas.canvasy(0) - region[1] + pixels) /
                                max(1, region[3] - region[1]))
        return "break"
