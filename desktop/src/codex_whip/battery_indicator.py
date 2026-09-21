from __future__ import annotations

import tkinter as tk


class BatteryIndicator(tk.Canvas):
    """Compact capsule battery with a percentage-only hover label."""

    NORMAL = "#1D1D1F"
    CHARGING = "#237C4B"
    LOW = "#BC3434"

    def __init__(self, parent: tk.Misc, *, font: str) -> None:
        super().__init__(
            parent,
            width=70,
            height=28,
            bg=parent.cget("bg"),
            highlightthickness=0,
            bd=0,
            takefocus=0,
        )
        self._font = font
        self.percent: int | None = None
        self.charging = False
        self._tooltip: tk.Toplevel | None = None
        self.bind("<Enter>", self._show_tooltip)
        self.bind("<Leave>", self._hide_tooltip)
        self.bind("<Destroy>", self._hide_tooltip)
        self._draw()

    @staticmethod
    def _capsule_points(x1: float, y1: float, x2: float, y2: float) -> tuple[float, ...]:
        radius = (y2 - y1) / 2
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
        color = self.color
        self.create_polygon(
            self._capsule_points(1.5, 3.5, 61.5, 24.5),
            smooth=True,
            splinesteps=24,
            fill="",
            outline=color,
            width=2,
            tags="body",
        )
        self.create_rectangle(63, 9, 67, 19, fill=color, outline="", tags="terminal")
        if self.charging:
            self.create_polygon(
                34, 6.5, 27.5, 15, 32.5, 15, 29.5, 22,
                40, 12, 34.5, 12,
                fill=color,
                outline="",
                tags="bolt",
            )
            return
        text = "--%" if self.percent is None else f"{self.percent}%"
        self.create_text(
            31.5,
            14,
            text=text,
            fill=color,
            font=(self._font, 9, "bold"),
            tags="percentage",
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
