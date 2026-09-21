from __future__ import annotations

import tkinter as tk


class BatteryIndicator(tk.Canvas):
    """iOS-style status battery; exact percentage is available on hover."""

    NORMAL = "#1D1D1F"
    CHARGING = "#237C4B"
    LOW = "#BC3434"
    UNKNOWN = "#8E8E93"

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
        self.percent: int | None = None
        self.charging = False
        self._tooltip: tk.Toplevel | None = None
        self.bind("<Enter>", self._show_tooltip)
        self.bind("<Leave>", self._hide_tooltip)
        self.bind("<Destroy>", self._hide_tooltip)
        self._draw()

    @staticmethod
    def _capsule_points(x1: float, y1: float, x2: float, y2: float) -> tuple[float, ...]:
        radius = min((y2 - y1) / 2, (x2 - x1) / 2)
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
        color = self.color if self.percent is not None else self.UNKNOWN
        self.create_polygon(
            self._capsule_points(1.5, 3, 26.5, 15),
            smooth=True,
            splinesteps=24,
            fill="",
            outline=color,
            width=1.4,
            tags="body",
        )
        self.create_oval(28, 6.5, 31, 11.5, fill=color, outline="", tags="terminal")

        if self.percent is not None and self.percent > 0:
            fill_left = 3.6
            fill_right = fill_left + (24.4 - fill_left) * self.percent / 100
            self.create_polygon(
                self._capsule_points(fill_left, 5.1, fill_right, 12.9),
                smooth=True,
                splinesteps=24,
                fill=color,
                outline="",
                tags="level",
            )

        if self.charging:
            background = self.cget("bg")
            self.create_polygon(
                15.5, 4.6, 11.7, 9.3, 14.5, 9.3,
                12.8, 13.6, 19, 7.9, 15.8, 7.9,
                fill=background,
                outline="",
                tags="bolt",
            )

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
