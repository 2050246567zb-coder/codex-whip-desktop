"""Shared drawing only; both views display the same immutable whip pose."""
from __future__ import annotations
import math
import tkinter as tk
from .effects import WhipPose, CartoonWhipPhysics, catmull_rom_points


class WhipDrawing:
    def __init__(self, canvas: tk.Canvas):
        self.canvas = canvas
        before = set(canvas.find_all())
        self._cord_outline_segments = tuple(
            self.canvas.create_line(
                0, 0, 0, 0, fill="#000103", width=10,
                capstyle=tk.ROUND, joinstyle=tk.ROUND
            )
            for _ in range(CartoonWhipPhysics.SEGMENTS - 1)
        )
        self._cord_segments = tuple(
            self.canvas.create_line(
                0, 0, 0, 0, fill="#090B0E", width=7,
                capstyle=tk.ROUND, joinstyle=tk.ROUND
            )
            for _ in range(CartoonWhipPhysics.SEGMENTS - 1)
        )
        self._cord_highlight_segments = tuple(
            self.canvas.create_line(
                0, 0, 0, 0, fill="#30343A", width=2,
                capstyle=tk.ROUND, joinstyle=tk.ROUND
            )
            for _ in range(CartoonWhipPhysics.SEGMENTS - 1)
        )
        self._handle_outline = self.canvas.create_line(
            0, 0, 0, 0, fill="#000103", width=12, capstyle=tk.ROUND
        )
        self._handle = self.canvas.create_line(
            0, 0, 0, 0, fill="#0A0C10", width=8, capstyle=tk.ROUND
        )
        self._handle_highlight = self.canvas.create_line(
            0, 0, 0, 0, fill="#343840", width=2, capstyle=tk.ROUND
        )
        self._grip_wraps = tuple(
            self.canvas.create_line(0, 0, 0, 0, fill="#0A0C10", width=1, state="hidden")
            for _ in range(7)
        )
        self._pommel_outer = self.canvas.create_oval(
            0, 0, 0, 0, fill="#000103", outline="#000103", width=1,
            state="hidden"
        )
        self._pommel_inner = self.canvas.create_oval(
            0, 0, 0, 0, fill="#0A0C10", outline="#0A0C10", width=1,
            state="hidden"
        )
        self._collar = self.canvas.create_oval(
            0, 0, 0, 0, fill="#06080B", outline="#06080B", width=1,
            state="hidden"
        )
        self.items = tuple(item for item in canvas.find_all() if item not in before)
        self._base_widths = {item: float(canvas.itemcget(item, "width")) for item in self.items}
        self._base_states = {item: canvas.itemcget(item, "state") or "normal" for item in self.items}
        self._last_scale = 1.0
        self._hidden = False
        self._style_cache = {}

    def _configure(self, item, **options):
        if self._style_cache.get(item) != options:
            self.canvas.itemconfigure(item, **options)
            self._style_cache[item] = options

    @staticmethod
    def _flatten(points: tuple[Point, ...]) -> tuple[float, ...]:
        return tuple(coordinate for point in points for coordinate in point)

    def _draw_pose(self, pose: WhipPose, scale: float = 1.0) -> None:
        samples = 10  # Twice the curve samples, without more physics nodes.
        smoothed = catmull_rom_points(pose.cord, samples)
        segment_count = max(1, len(pose.cord) - 1)
        for index, (outline, cord, highlight) in enumerate(
            zip(
                self._cord_outline_segments,
                self._cord_segments,
                self._cord_highlight_segments,
            )
        ):
            if index >= segment_count:
                self._configure(outline, state="hidden")
                self._configure(cord, state="hidden")
                self._configure(highlight, state="hidden")
                continue
            curve = smoothed[index * samples : (index + 1) * samples + 1]
            coordinates = self._flatten(curve)
            progress = index / max(1, segment_count - 1)
            extra = 2.0 if index < 2 else 0.0
            body_width = 7.0 - progress * 4.5 + extra
            self.canvas.coords(outline, *coordinates)
            self.canvas.coords(cord, *coordinates)
            self.canvas.coords(highlight, *coordinates)
            self._configure(
                outline, width=(body_width + 2.5) * scale, state="normal"
            )
            self._configure(cord, width=body_width * scale, state="normal")
            self._configure(
                highlight,
                width=max(1.0, body_width * 0.24) * scale,
                state="normal",
            )

        handle = (*pose.handle_start, *pose.handle_end)
        self.canvas.coords(self._handle_outline, *handle)
        self.canvas.coords(self._handle, *handle)
        for item in (self._handle_outline, self._handle, self._handle_highlight):
            self._configure(item, width=self._base_widths[item] * scale, state="normal")
        start_x, start_y = pose.handle_start
        end_x, end_y = pose.handle_end
        dx = end_x - start_x
        dy = end_y - start_y
        length = math.hypot(dx, dy) or 1.0
        normal_x = -dy / length
        normal_y = dx / length
        self.canvas.coords(
            self._handle_highlight,
            start_x + normal_x * 1.8 * scale,
            start_y + normal_y * 1.8 * scale,
            end_x + normal_x * 1.8 * scale,
            end_y + normal_y * 1.8 * scale,
        )
        for index, wrap in enumerate(self._grip_wraps):
            progress = 0.16 + index * 0.095
            center_x = start_x + dx * progress
            center_y = start_y + dy * progress
            self.canvas.coords(
                wrap,
                center_x - normal_x * 9,
                center_y - normal_y * 9,
                center_x + normal_x * 9,
                center_y + normal_y * 9,
            )
        self.canvas.coords(
            self._pommel_outer,
            start_x - 13,
            start_y - 13,
            start_x + 13,
            start_y + 13,
        )
        self.canvas.coords(
            self._pommel_inner,
            start_x - 7,
            start_y - 7,
            start_x + 7,
            start_y + 7,
        )
        self.canvas.coords(
            self._collar,
            end_x - 11,
            end_y - 11,
            end_x + 11,
            end_y + 11,
        )

    def draw(self, pose: WhipPose, *, scale: float = 1.0, offset=(0.0, 0.0)) -> None:
        self._hidden = False
        self._last_scale = scale
        if scale != 1.0 or offset != (0.0, 0.0):
            def transform(point):
                return point[0] * scale + offset[0], point[1] * scale + offset[1]
            pose = WhipPose(transform(pose.handle_start), transform(pose.handle_end),
                            tuple(transform(point) for point in pose.cord))
        self._draw_pose(pose, scale)

    def hide(self):
        self._hidden = True
        for item in self.items:
            self.canvas.itemconfigure(item, state="hidden")
        self._style_cache.clear()
