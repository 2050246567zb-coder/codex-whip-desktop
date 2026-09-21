"""Shared drawing only; both views display the same immutable whip pose."""
from __future__ import annotations
import math
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk
from .effects import WhipPose, CartoonWhipPhysics, catmull_rom_points


class WhipDrawing:
    RENDER_SECTIONS = 5

    def __init__(self, canvas: tk.Canvas):
        self.canvas = canvas
        before = set(canvas.find_all())
        self._cord_outline_segments = tuple(
            self.canvas.create_line(
                0, 0, 0, 0, fill="#000103", width=10,
                capstyle=tk.ROUND, joinstyle=tk.ROUND
            )
            for _ in range(self.RENDER_SECTIONS)
        )
        self._cord_segments = tuple(
            self.canvas.create_line(
                0, 0, 0, 0, fill="#090B0E", width=7,
                capstyle=tk.ROUND, joinstyle=tk.ROUND
            )
            for _ in range(self.RENDER_SECTIONS)
        )
        self._cord_highlight_segments = tuple(
            self.canvas.create_line(
                0, 0, 0, 0, fill="#30343A", width=2,
                capstyle=tk.ROUND, joinstyle=tk.ROUND
            )
            for _ in range(self.RENDER_SECTIONS)
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
        self.cord_opacity = 1.0

    def _configure(self, item, **options):
        if self._style_cache.get(item) != options:
            self.canvas.itemconfigure(item, **options)
            self._style_cache[item] = options

    def fade_cord(self, opacity, background):
        """Optional home-view fade; overlay rendering keeps its existing style."""
        self.cord_opacity = max(0.0, min(1.0, float(opacity)))
        bg = self.canvas.winfo_rgb(background)
        for items, color in ((self._cord_outline_segments, '#000103'),
                             (self._cord_segments, '#090B0E'),
                             (self._cord_highlight_segments, '#30343A')):
            fg = self.canvas.winfo_rgb(color)
            fill = '#' + ''.join(f'{round((a*opacity+b*(1-opacity))/257):02x}' for a,b in zip(fg,bg))
            for item in items:
                self.canvas.itemconfigure(item, fill=fill)

    @property
    def display_handle(self):
        return tuple(self.canvas.coords(self._handle))

    @property
    def visible(self):
        return self.canvas.itemcget(self._handle, "state") != "hidden"

    @staticmethod
    def _flatten(points: tuple[Point, ...]) -> tuple[float, ...]:
        return tuple(coordinate for point in points for coordinate in point)

    def _draw_pose(self, pose: WhipPose, scale: float = 1.0) -> None:
        # Draw a few long, overlapping splines instead of one Canvas item per
        # physics link.  The old 21-piece lash exposed a dark round cap at each
        # joint on Windows and looked visibly grainy when scaled over Codex.
        smoothed = catmull_rom_points(pose.cord, 18)
        point_count = len(smoothed)
        for index, (outline, cord, highlight) in enumerate(
            zip(
                self._cord_outline_segments,
                self._cord_segments,
                self._cord_highlight_segments,
            )
        ):
            if point_count < 2:
                self._configure(outline, state="hidden")
                self._configure(cord, state="hidden")
                self._configure(highlight, state="hidden")
                continue
            start = round(index * (point_count - 1) / self.RENDER_SECTIONS)
            end = round((index + 1) * (point_count - 1) / self.RENDER_SECTIONS)
            if index:
                start = max(0, start - 2)
            if index + 1 < self.RENDER_SECTIONS:
                end = min(point_count - 1, end + 2)
            curve = smoothed[start : end + 1]
            coordinates = self._flatten(curve)
            progress = index / max(1, self.RENDER_SECTIONS - 1)
            extra = 2.0 if index == 0 else 0.0
            body_width = 7.0 - progress * 4.5 + extra
            self.canvas.coords(outline, *coordinates)
            self.canvas.coords(cord, *coordinates)
            self.canvas.coords(highlight, *coordinates)
            self._configure(
                outline, width=(body_width + 2.5) * scale, state="normal",
                smooth=True, splinesteps=36,
            )
            self._configure(
                cord, width=body_width * scale, state="normal",
                smooth=True, splinesteps=36,
            )
            self._configure(
                highlight,
                width=max(1.0, body_width * 0.24) * scale,
                state="normal",
                smooth=True,
                splinesteps=36,
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


class SupersampledWhipDrawing:
    """Four-times supersampled home renderer with smooth transparent edges.

    Tk Canvas paths are fast but their Windows rasterizer does not antialias.
    The home view can use an RGBA image safely, unlike the color-keyed desktop
    overlay where partially transparent pixels would reveal the key colour.
    """

    RENDER_SECTIONS = WhipDrawing.RENDER_SECTIONS

    def __init__(self, canvas: tk.Canvas, *, supersample: int = 4):
        self.canvas = canvas
        self.supersample = max(2, int(supersample))
        self._image_item = canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.items = (self._image_item,)
        self._photo = None
        self._pose = None
        self._hidden = False
        self.cord_opacity = 1.0
        self._background = canvas.cget("bg")

    @property
    def display_handle(self):
        if self._pose is None:
            return ()
        return (*self._pose.handle_start, *self._pose.handle_end)

    @property
    def visible(self):
        return self.canvas.itemcget(self._image_item, "state") != "hidden"

    @staticmethod
    def _rgba(color: str, opacity: float = 1.0):
        color = color.lstrip("#")
        return tuple(int(color[index:index + 2], 16) for index in (0, 2, 4)) + (
            round(255 * max(0.0, min(1.0, opacity))),
        )

    @staticmethod
    def _round_line(draw, points, *, fill, width):
        width = max(1, int(round(width)))
        if len(points) < 2:
            return
        draw.line(points, fill=fill, width=width, joint="curve")
        radius = width / 2
        for x, y in (points[0], points[-1]):
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=fill)

    @classmethod
    def _sections(cls, points):
        count = len(points)
        if count < 2:
            return ()
        sections = []
        for index in range(cls.RENDER_SECTIONS):
            start = round(index * (count - 1) / cls.RENDER_SECTIONS)
            end = round((index + 1) * (count - 1) / cls.RENDER_SECTIONS)
            if index:
                start = max(0, start - 2)
            if index + 1 < cls.RENDER_SECTIONS:
                end = min(count - 1, end + 2)
            progress = index / max(1, cls.RENDER_SECTIONS - 1)
            extra = 2.0 if index == 0 else 0.0
            sections.append((points[start:end + 1], 7.0 - progress * 4.5 + extra))
        return tuple(sections)

    def fade_cord(self, opacity, background):
        # Hero calls this before _draw_pose, so a frame is rendered only once.
        self.cord_opacity = max(0.0, min(1.0, float(opacity)))
        self._background = background

    def _draw_pose(self, pose: WhipPose, scale: float = 1.0) -> None:
        self._pose = pose
        self._hidden = False
        smoothed = catmull_rom_points(pose.cord, 18)
        all_points = list(smoothed) + [pose.handle_start, pose.handle_end]
        if not all_points:
            self.hide()
            return

        # A tight backing surface keeps four-times rendering fast enough for
        # the 60 Hz home animation while retaining generous antialias margins.
        margin = max(8, math.ceil(10 * scale))
        left = math.floor(min(point[0] for point in all_points) - margin)
        top = math.floor(min(point[1] for point in all_points) - margin)
        right = math.ceil(max(point[0] for point in all_points) + margin)
        bottom = math.ceil(max(point[1] for point in all_points) + margin)
        width, height = max(1, right - left), max(1, bottom - top)
        ss = self.supersample
        surface = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(surface)

        def local(points):
            return tuple(((x - left) * ss, (y - top) * ss) for x, y in points)

        sections = self._sections(smoothed)
        cord_layers = (
            ("#000103", lambda body: body + 2.5),
            ("#090B0E", lambda body: body),
            ("#30343A", lambda body: max(1.0, body * 0.24)),
        )
        for color, width_for in cord_layers:
            fill = self._rgba(color, self.cord_opacity)
            for points, body_width in sections:
                self._round_line(
                    draw,
                    local(points),
                    fill=fill,
                    width=width_for(body_width) * scale * ss,
                )

        start_x, start_y = pose.handle_start
        end_x, end_y = pose.handle_end
        handle = local((pose.handle_start, pose.handle_end))
        self._round_line(draw, handle, fill=self._rgba("#000103"), width=12 * scale * ss)
        self._round_line(draw, handle, fill=self._rgba("#0A0C10"), width=8 * scale * ss)
        dx, dy = end_x - start_x, end_y - start_y
        length = math.hypot(dx, dy) or 1.0
        normal_x, normal_y = -dy / length, dx / length
        highlight = local((
            (start_x + normal_x * 1.8 * scale, start_y + normal_y * 1.8 * scale),
            (end_x + normal_x * 1.8 * scale, end_y + normal_y * 1.8 * scale),
        ))
        self._round_line(draw, highlight, fill=self._rgba("#343840"), width=2 * scale * ss)

        surface = surface.resize((width, height), Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(surface, master=self.canvas)
        self.canvas.coords(self._image_item, left, top)
        self.canvas.itemconfigure(self._image_item, image=self._photo, state="normal")

    def draw(self, pose: WhipPose, *, scale: float = 1.0, offset=(0.0, 0.0)) -> None:
        if scale != 1.0 or offset != (0.0, 0.0):
            def transform(point):
                return point[0] * scale + offset[0], point[1] * scale + offset[1]
            pose = WhipPose(transform(pose.handle_start), transform(pose.handle_end),
                            tuple(transform(point) for point in pose.cord))
        self._draw_pose(pose, scale)

    def hide(self):
        self._hidden = True
        self.canvas.itemconfigure(self._image_item, state="hidden")
