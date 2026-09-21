from __future__ import annotations

import tkinter as tk

from PIL import Image, ImageDraw, ImageTk


class BatteryIndicator(tk.Canvas):
    """iOS-style status battery; exact percentage is available on hover."""

    OUTLINE = "#111111"
    NORMAL = "#111111"
    CHARGING = "#34C759"
    LOW = "#FF3B30"
    UNKNOWN = "#C7C7CC"

    def __init__(self, parent: tk.Misc, *, font: str) -> None:
        super().__init__(
            parent,
            width=32,
            height=18,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
            takefocus=0,
        )
        self._font = font
        self._glyph: ImageTk.PhotoImage | None = None
        self.percent: int | None = None
        self.charging = False
        self._tooltip: tk.Toplevel | None = None
        self.bind("<Enter>", self._show_tooltip)
        self.bind("<Leave>", self._hide_tooltip)
        self.bind("<Destroy>", self._hide_tooltip)
        self._draw()

    @staticmethod
    def _rounded_points(
        x1: float, y1: float, x2: float, y2: float, radius: float,
    ) -> tuple[float, ...]:
        radius = min(radius, (y2 - y1) / 2, (x2 - x1) / 2)
        return (
            x1 + radius, y1,
            x2 - radius, y1,
            x2, y1,
            x2, y1 + radius,
            x2, y2 - radius,
            x2, y2,
            x2 - radius, y2,
            x1 + radius, y2,
            x1, y2,
            x1, y2 - radius,
            x1, y1 + radius,
            x1, y1,
        )

    @property
    def color(self) -> str:
        if self.charging:
            return self.CHARGING
        if self.percent is not None and self.percent < 20:
            return self.LOW
        return self.NORMAL

    def set_status(self, percent: int | None, charging: bool = False) -> None:
        self.percent = None if percent is None else max(0, min(100, int(percent)))
        self.charging = bool(charging and self.percent is not None)
        self._draw()
        if self._tooltip is not None:
            self._tooltip_label().configure(text=self.tooltip_text)

    @property
    def tooltip_text(self) -> str:
        return (
            f"剩余电量 {self.percent}%"
            if self.percent is not None
            else "等待手柄电量"
        )

    def _draw(self) -> None:
        self.delete("all")
        # Render at 6x and downsample. Tk Canvas primitives are visibly jagged
        # at this 32 px status-icon size; supersampling matches Apple's soft,
        # optically even battery outline much more closely.
        scale = 6
        image = Image.new("RGBA", (32 * scale, 18 * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)

        def box(values):
            return tuple(round(value * scale) for value in values)

        outline = self.OUTLINE if self.percent is not None else self.UNKNOWN
        stroke = max(1, round(1.35 * scale))
        draw.rounded_rectangle(
            box((1.5, 2.5, 26.5, 15.5)),
            radius=3.4 * scale,
            fill=(255, 255, 255, 0),
            outline=outline,
            width=stroke,
        )
        draw.rounded_rectangle(
            box((28.0, 6.3, 31.0, 11.7)),
            radius=1.4 * scale,
            fill=outline,
        )

        if self.percent is not None and self.percent > 0:
            fill_left = 3.6
            fill_right = fill_left + (24.4 - fill_left) * self.percent / 100
            draw.rounded_rectangle(
                box((fill_left, 4.6, fill_right, 13.4)),
                radius=min(2.2, max(0.4, (fill_right - fill_left) / 2)) * scale,
                fill=self.color,
            )

        if self.charging:
            bolt = tuple(
                (round(x * scale), round(y * scale))
                for x, y in (
                    (16.2, 2.1), (10.8, 9.0), (14.5, 9.0),
                    (12.9, 15.8), (20.7, 7.1), (16.8, 7.1),
                )
            )
            draw.polygon(bolt, fill="#FFFFFF")
            draw.line(
                (*bolt, bolt[0]), fill=self.CHARGING,
                width=max(1, round(1.05 * scale)), joint="curve",
            )

        image = image.resize((32, 18), Image.Resampling.LANCZOS)
        self._glyph = ImageTk.PhotoImage(image, master=self)
        self.create_image(0, 0, anchor="nw", image=self._glyph, tags="glyph")

    def _tooltip_label(self) -> tk.Label:
        assert self._tooltip is not None
        return self._tooltip.winfo_children()[0]

    def _show_tooltip(self, _event=None) -> None:
        self._hide_tooltip()
        tip = tk.Toplevel(self)
        tip.withdraw()
        tip.overrideredirect(True)
        try:
            tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            tip,
            text=self.tooltip_text,
            bg="#1D1D1F",
            fg="#FFFFFF",
            padx=10,
            pady=6,
            bd=0,
            font=(self._font, 9),
        )
        label.pack()
        tip.update_idletasks()
        tip.geometry(f"+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height() + 6}")
        tip.deiconify()
        self._tooltip = tip

    def _hide_tooltip(self, _event=None) -> None:
        tooltip, self._tooltip = self._tooltip, None
        if tooltip is not None:
            try:
                tooltip.destroy()
            except tk.TclError:
                pass
