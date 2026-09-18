from __future__ import annotations

import ctypes
import io
import json
import math
import os
import random
import sys
import threading
import time
import tkinter as tk
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

from .sensor_pose import SensorPose, SensorPoseTracker
from .paths import user_data_dir

if os.name == "nt":
    from ctypes import wintypes


Point = tuple[float, float]
LogHandler = Callable[[str], None]
SoundHandler = Callable[[bytes], bool]
ManualWhipHandler = Callable[[Point], None]


@dataclass(frozen=True, slots=True)
class WhipPose:
    handle_start: Point
    handle_end: Point
    cord: tuple[Point, ...]


def extend_whip_pose(
    pose: WhipPose,
    factor: float,
    *,
    x_offset: float = 0.0,
    y_offset: float = 0.0,
) -> WhipPose:
    """Lengthen every cord vector while leaving the handle itself unchanged."""
    anchor_x, anchor_y = pose.cord[0]

    def shift(point: Point) -> Point:
        return point[0] + x_offset, point[1] + y_offset

    cord = tuple(
        (
            anchor_x + (point[0] - anchor_x) * factor + x_offset,
            anchor_y + (point[1] - anchor_y) * factor + y_offset,
        )
        for point in pose.cord
    )
    return WhipPose(shift(pose.handle_start), shift(pose.handle_end), cord)


@dataclass(frozen=True, slots=True)
class WindowRectangle:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class WhipParkingPosition:
    """Handle position stored as a ratio of the current Codex window."""

    horizontal: float
    vertical: float

    def normalized(self) -> "WhipParkingPosition":
        return WhipParkingPosition(
            min(1.0, max(0.0, float(self.horizontal))),
            min(1.0, max(0.0, float(self.vertical))),
        )


@dataclass(slots=True)
class HealingDamage:
    """A window-local tear mask that disappears exactly three seconds later."""

    left: int
    top: int
    mask: Image.Image
    created_at: float
    seed: int


@dataclass(slots=True)
class PhysicsPoint:
    x: float
    y: float
    previous_x: float
    previous_y: float


class CartoonWhipPhysics:
    """VibeWhip-style constrained rope tuned for this small Tk overlay."""

    SEGMENTS = 22
    CONSTRAINT_ITERATIONS = 18
    BASE_ANGLE = 2.40
    CORD_LENGTH_MULTIPLIER = 1.50
    CORD_WEIGHT_MULTIPLIER = 1.20
    INERTIAL_DAMPING = 0.80
    CORD_ANCHOR_FOLLOW = 0.40
    VELOCITY_SLEEP_THRESHOLD = 0.065
    FIXED_STEP_SECONDS = 1.0 / 60.0
    GRAVITY_PER_STEP = 1.60  # 3.33x the previous 0.48, without increasing inertia.

    def __init__(self, anchor: Point, *, scale: float = 1.0) -> None:
        self.scale = min(1.0, max(0.5, float(scale)))
        self.position = (float(anchor[0]), float(anchor[1]))
        self._last_anchor = self.position
        self._last_aim_offset_degrees = 0.0
        self.velocity = (0.0, 0.0)
        self.handle_angle = self.BASE_ANGLE
        self.handle_angular_velocity = 0.0
        self.points: list[PhysicsPoint] = []
        self._accumulator = 0.0
        self._reset_rope()

    def pose(self) -> WhipPose:
        return WhipPose(self.position, self._handle_end(),
                        tuple((p.x, p.y) for p in self.points))

    def adopt_pose(self, pose: WhipPose) -> None:
        """Hand an animated stroke back to gravity without resetting its shape."""
        pose = _resample_whip_pose(pose, self.SEGMENTS)
        self.position = self._last_anchor = pose.handle_start
        self.handle_angle = math.atan2(pose.handle_end[1] - pose.handle_start[1],
                                       pose.handle_end[0] - pose.handle_start[0])
        self.velocity = (0.0, 0.0)
        self.handle_angular_velocity = 0.0
        self.points = [PhysicsPoint(x, y, x, y) for x, y in pose.cord]
        self._accumulator = 0.0

    def move_without_impulse(self, anchor: Point, aim_offset_degrees: float) -> None:
        """A display-origin change is a rigid transform, not a physical pull.

        Transform current AND previous Verlet points together to preserve the
        existing velocities. Gravity/constraints still run on the next step.
        """
        old = self.position
        angle = self._wrap_angle(self.BASE_ANGLE + math.radians(aim_offset_degrees) * .72)
        delta = self._wrap_angle(angle - self.handle_angle)
        c, s = math.cos(delta), math.sin(delta)
        def transform(x: float, y: float) -> Point:
            x, y = x-old[0], y-old[1]
            return anchor[0]+x*c-y*s, anchor[1]+x*s+y*c
        for point in self.points:
            point.x, point.y = transform(point.x, point.y)
            point.previous_x, point.previous_y = transform(point.previous_x, point.previous_y)
        vx, vy = self.velocity
        self.velocity = (vx*c-vy*s, vx*s+vy*c)
        self.position = self._last_anchor = anchor
        self.handle_angle = angle
        self._last_aim_offset_degrees = float(aim_offset_degrees)

    @property
    def handle_length(self) -> float:
        return 103.0 * self.scale

    def _segment_length(self, index: int) -> float:
        progress = index / max(1, self.SEGMENTS - 2)
        return (
            (15.5 - progress * 7.0)
            * self.CORD_LENGTH_MULTIPLIER
            * self.scale
        )

    def _handle_end(self) -> Point:
        return (
            self.position[0] + math.cos(self.handle_angle) * self.handle_length,
            self.position[1] + math.sin(self.handle_angle) * self.handle_length,
        )

    def _reset_rope(self) -> None:
        self.points.clear()
        x, y = self._handle_end()
        self.points.append(PhysicsPoint(x, y, x, y))
        for index in range(1, self.SEGMENTS):
            progress = index / (self.SEGMENTS - 1)
            # Start in a hanging curve instead of an upward coil. This avoids a
            # synthetic launch during the first seconds after the overlay opens.
            direction = self.BASE_ANGLE - progress * 0.75
            x += math.cos(direction) * self._segment_length(index - 1)
            y += math.sin(direction) * self._segment_length(index - 1)
            self.points.append(PhysicsPoint(x, y, x, y))

    @staticmethod
    def _wrap_angle(value: float) -> float:
        return (value + math.pi) % (math.pi * 2.0) - math.pi

    def _apply_base_pose(self, handle_end: Point) -> None:
        direction_x = math.cos(self.handle_angle)
        direction_y = math.sin(self.handle_angle)
        self.points[0].x, self.points[0].y = handle_end
        for index in range(1, min(3, len(self.points))):
            stiffness = 0.90 if index == 1 else 0.80
            previous = self.points[index - 1]
            point = self.points[index]
            target_length = self._segment_length(index - 1)
            target_x = previous.x + direction_x * target_length
            target_y = previous.y + direction_y * target_length
            point.x += (target_x - point.x) * stiffness
            point.y += (target_y - point.y) * stiffness

    def _apply_bend_limits(self) -> None:
        for index in range(1, len(self.points) - 1):
            first = self.points[index - 1]
            center = self.points[index]
            last = self.points[index + 1]
            first_x, first_y = first.x - center.x, first.y - center.y
            last_x, last_y = last.x - center.x, last.y - center.y
            first_length = math.hypot(first_x, first_y) or 0.0001
            last_length = math.hypot(last_x, last_y) or 0.0001
            first_x, first_y = first_x / first_length, first_y / first_length
            last_x, last_y = last_x / last_length, last_y / last_length
            dot = min(1.0, max(-1.0, first_x * last_x + first_y * last_y))
            bend = math.pi - math.acos(dot)
            progress = index / max(1, len(self.points) - 2)
            max_bend = math.radians(16.0 + progress * 114.0)
            if bend <= max_bend:
                continue
            cross = first_x * last_y - first_y * last_x
            target_angle = math.atan2(first_y, first_x) + (
                math.pi - max_bend if cross >= 0 else -(math.pi - max_bend)
            )
            target_x = center.x + math.cos(target_angle) * last_length
            target_y = center.y + math.sin(target_angle) * last_length
            rigidity = 0.80 - progress * 0.68
            last.x += (target_x - last.x) * rigidity
            last.y += (target_y - last.y) * rigidity

    def _cap_stretch(self) -> None:
        for index in range(len(self.points) - 1):
            first = self.points[index]
            second = self.points[index + 1]
            dx = second.x - first.x
            dy = second.y - first.y
            distance = math.hypot(dx, dy) or 0.0001
            maximum = self._segment_length(index) * 1.20
            if distance > maximum:
                scale = maximum / distance
                second.x = first.x + dx * scale
                second.y = first.y + dy * scale

    def step(
        self,
        anchor: Point,
        dt_seconds: float = 1 / 60,
        *,
        aim_offset_degrees: float = 0.0,
        freeze: bool = False,
        kinematic: bool = False,
    ) -> WhipPose:
        # Verlet stores displacement from the previous SIMULATION step, not a
        # velocity. Variable-sized render substeps used to damp that displacement
        # again, making a 16 ms callback much floatier than a 16.667 ms callback.
        if freeze:
            self._accumulator = 0.0
            return self._step(anchor, self.FIXED_STEP_SECONDS,
                              aim_offset_degrees=aim_offset_degrees,
                              freeze=True, kinematic=kinematic)
        self._accumulator += min(0.10, max(0.0, dt_seconds))
        count = int((self._accumulator + 1e-9) / self.FIXED_STEP_SECONDS)
        self._accumulator = max(0.0, self._accumulator - count * self.FIXED_STEP_SECONDS)
        for _ in range(count):
            self._step(anchor, self.FIXED_STEP_SECONDS, aim_offset_degrees=aim_offset_degrees,
                       freeze=False, kinematic=kinematic)
        return self.pose()

    def _step(
        self, anchor: Point, dt_seconds: float, *,
        aim_offset_degrees: float, freeze: bool, kinematic: bool,
    ) -> WhipPose:
        anchor = (float(anchor[0]), float(anchor[1]))
        if freeze:
            translation_x = anchor[0] - self._last_anchor[0]
            translation_y = anchor[1] - self._last_anchor[1]
            aim_delta = math.radians(
                aim_offset_degrees - self._last_aim_offset_degrees
            ) * 0.72
            old_handle_end = self._handle_end()
            self.position = (
                self.position[0] + translation_x,
                self.position[1] + translation_y,
            )
            self.velocity = (0.0, 0.0)
            self.handle_angle = self._wrap_angle(self.handle_angle + aim_delta)
            self.handle_angular_velocity = 0.0
            new_handle_end = self._handle_end()
            cosine = math.cos(aim_delta)
            sine = math.sin(aim_delta)
            for point in self.points:
                relative_x = point.x - old_handle_end[0]
                relative_y = point.y - old_handle_end[1]
                point.x = (
                    new_handle_end[0]
                    + relative_x * cosine
                    - relative_y * sine
                )
                point.y = (
                    new_handle_end[1]
                    + relative_x * sine
                    + relative_y * cosine
                )
                point.previous_x = point.x
                point.previous_y = point.y
            self.points[0].x, self.points[0].y = new_handle_end
            self.points[0].previous_x, self.points[0].previous_y = new_handle_end
            self._last_anchor = anchor
            self._last_aim_offset_degrees = float(aim_offset_degrees)
            return WhipPose(
                self.position,
                new_handle_end,
                tuple((point.x, point.y) for point in self.points),
            )

        frame = min(2.0, max(0.25, dt_seconds * 60.0))
        x, y = self.position
        velocity_x, velocity_y = self.velocity
        velocity_x = (velocity_x + (anchor[0] - x) * 0.26 * frame) * (0.61 ** frame)
        velocity_y = (velocity_y + (anchor[1] - y) * 0.26 * frame) * (0.61 ** frame)
        x += velocity_x * frame
        y += velocity_y * frame
        if kinematic:
            # The measured grip is an input, never a mass driven by the rope.
            x, y = anchor
            velocity_x = velocity_y = 0.0
        translation_x = (x - self.position[0]) * self.CORD_ANCHOR_FOLLOW
        translation_y = (y - self.position[1]) * self.CORD_ANCHOR_FOLLOW
        self.position = (x, y)
        self.velocity = (velocity_x, velocity_y)
        # Carry part of the cord with the handle without injecting extra
        # Verlet velocity. This lowers the apparent rope inertia while keeping
        # the existing damping curve that already settles cleanly at rest.
        for point in self.points:
            point.x += translation_x
            point.y += translation_y
            point.previous_x += translation_x
            point.previous_y += translation_y

        movement_aim = min(1.15, max(-1.15, -velocity_x * 0.018 + velocity_y * 0.010))
        target_angle = (
            self.BASE_ANGLE + movement_aim + math.radians(aim_offset_degrees) * 0.72
        )
        error = self._wrap_angle(target_angle - self.handle_angle)
        self.handle_angular_velocity += error * 0.70
        self.handle_angular_velocity *= 0.078
        self.handle_angle = self._wrap_angle(
            self.handle_angle + self.handle_angular_velocity * frame
        )
        if kinematic:
            self.handle_angle = self._wrap_angle(target_angle)
            self.handle_angular_velocity = 0.0
        handle_end = self._handle_end()

        for point in self.points[1:]:
            inertial_x = (point.x - point.previous_x) * (
                self.INERTIAL_DAMPING ** frame
            )
            inertial_y = (point.y - point.previous_y) * (
                self.INERTIAL_DAMPING ** frame
            )
            if abs(inertial_x) < self.VELOCITY_SLEEP_THRESHOLD:
                inertial_x = 0.0
            if abs(inertial_y) < self.VELOCITY_SLEEP_THRESHOLD:
                inertial_y = 0.0
            point.previous_x, point.previous_y = point.x, point.y
            point.x += inertial_x * frame
            point.y += (
                inertial_y * frame
                + self.GRAVITY_PER_STEP
                * self.CORD_WEIGHT_MULTIPLIER
                * self.scale
                * frame
                * frame
            )

        self.points[0].x, self.points[0].y = handle_end
        self.points[0].previous_x, self.points[0].previous_y = handle_end
        self._cap_stretch()
        self._apply_base_pose(handle_end)
        for _ in range(self.CONSTRAINT_ITERATIONS):
            self.points[0].x, self.points[0].y = handle_end
            for index in range(len(self.points) - 1):
                first = self.points[index]
                second = self.points[index + 1]
                dx = second.x - first.x
                dy = second.y - first.y
                distance = math.hypot(dx, dy) or 0.0001
                correction = (distance - self._segment_length(index)) / distance
                if index == 0:
                    second.x -= dx * correction
                    second.y -= dy * correction
                else:
                    correction_x = dx * correction * 0.5
                    correction_y = dy * correction * 0.5
                    first.x += correction_x
                    first.y += correction_y
                    second.x -= correction_x
                    second.y -= correction_y
            self._apply_bend_limits()
            self._apply_base_pose(handle_end)
            self._cap_stretch()

        # Gravity must speed up the fall, not stretch the rope. Finish the PBD
        # iterations with a rooted length projection (the grip cannot move).
        for index in range(len(self.points) - 1):
            first, second = self.points[index:index+2]
            dx, dy = second.x-first.x, second.y-first.y
            length = math.hypot(dx, dy) or 1.0
            scale = self._segment_length(index) / length
            second.x, second.y = first.x+dx*scale, first.y+dy*scale
        self._last_anchor = anchor
        self._last_aim_offset_degrees = float(aim_offset_degrees)
        return WhipPose(
            self.position,
            handle_end,
            tuple((point.x, point.y) for point in self.points),
        )


def healing_visibility(
    age_seconds: float,
    quiet_seconds: float | None = None,
) -> float:
    """Open quickly; start closing only after three seconds without a strike."""
    age = max(0.0, float(age_seconds))
    if age < 0.14:
        opening = cubic_bezier_ease_in_out(age / 0.14)
    else:
        opening = 1.0
    quiet = age if quiet_seconds is None else max(0.0, float(quiet_seconds))
    if quiet <= 3.0:
        healing = 1.0
    elif quiet < 3.85:
        healing = 1.0 - cubic_bezier_ease_in_out((quiet - 3.0) / 0.85)
    else:
        healing = 0.0
    return opening * healing


def build_tear_mask(size: tuple[int, int], seed: int) -> Image.Image:
    """Create a long irregular split mask without drawing a procedural PCB."""
    width, height = size
    random_source = random.Random(seed)
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    count = 19
    center_y = height * 0.50
    centerline: list[Point] = []
    for index in range(count):
        progress = index / (count - 1)
        x = width * (0.035 + progress * 0.93)
        curve = math.sin((progress - 0.1) * math.pi * 1.18) * height * 0.075
        jitter = random_source.uniform(-0.065, 0.065) * height
        centerline.append((x, center_y + curve + jitter))

    top: list[Point] = []
    bottom: list[Point] = []
    for index, (x, y) in enumerate(centerline):
        progress = index / (count - 1)
        taper = max(0.12, math.sin(progress * math.pi) ** 0.72)
        half_gap = height * random_source.uniform(0.105, 0.19) * taper
        top.append((x, y - half_gap * random_source.uniform(0.75, 1.25)))
        bottom.append((x, y + half_gap * random_source.uniform(0.75, 1.25)))
    draw.polygon(top + list(reversed(bottom)), fill=255)
    for index in range(3, count - 3, 4):
        x, y = centerline[index]
        branch_y = y + random_source.choice((-1, 1)) * random_source.uniform(0.16, 0.30) * height
        draw.line((x, y, x + random_source.uniform(0.03, 0.08) * width, branch_y), fill=210, width=2)
    return mask.filter(ImageFilter.GaussianBlur(0.7))


def build_directional_tear_mask(
    size: tuple[int, int],
    seed: int,
    direction: Point,
) -> Image.Image:
    """Rotate the long tear axis into the displayed whip-tip travel direction."""
    mask = build_tear_mask(size, seed)
    direction_x, direction_y = float(direction[0]), float(direction[1])
    if math.hypot(direction_x, direction_y) <= 0.001:
        return mask
    screen_angle = math.degrees(math.atan2(direction_y, direction_x))
    # Pillow rotates counter-clockwise while screen Y grows downward.
    return mask.rotate(
        -screen_angle,
        resample=Image.Resampling.BICUBIC,
        expand=True,
        fillcolor=0,
    )


def cover_image(
    source: Image.Image,
    size: tuple[int, int],
    *,
    overscan: float = 1.08,
) -> Image.Image:
    """Return one centered PCB backplane that fully covers the target window."""
    width, height = size
    source_rgb = source.convert("RGB")
    scale = max(width / source_rgb.width, height / source_rgb.height) * max(
        1.0, float(overscan)
    )
    resized = source_rgb.resize(
        (
            max(width, round(source_rgb.width * scale)),
            max(height, round(source_rgb.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def merge_damage_masks(
    size: tuple[int, int],
    masks: list[tuple[int, int, Image.Image]],
    *,
    proximity: int = 24,
) -> Image.Image:
    """Union nearby tears into one continuous PCB reveal mask."""
    combined = Image.new("L", size, 0)
    width, height = size
    for left, top, mask in masks:
        if mask.mode != "L":
            mask = mask.convert("L")
        paste_left = max(0, int(left))
        paste_top = max(0, int(top))
        paste_right = min(width, int(left) + mask.width)
        paste_bottom = min(height, int(top) + mask.height)
        if paste_right <= paste_left or paste_bottom <= paste_top:
            continue
        source = mask.crop(
            (
                paste_left - int(left),
                paste_top - int(top),
                paste_right - int(left),
                paste_bottom - int(top),
            )
        )
        existing = combined.crop((paste_left, paste_top, paste_right, paste_bottom))
        combined.paste(
            ImageChops.lighter(existing, source),
            (paste_left, paste_top),
        )

    if combined.getbbox() is None or proximity <= 0 or len(masks) < 2:
        return combined

    # Connect grouped wounds geometrically instead of running a large-radius
    # morphology filter across the whole raster. The bridge sits underneath the
    # union, so nearby damage expands into one continuous visible area.
    bridge_draw = ImageDraw.Draw(combined)
    boxes: list[tuple[int, int, int, int]] = []
    for left, top, mask in masks:
        local = mask.getbbox()
        if local is None:
            boxes.append((left, top, left, top))
        else:
            boxes.append(
                (
                    left + local[0],
                    top + local[1],
                    left + local[2],
                    top + local[3],
                )
            )
    def masks_intersect(first_index: int, second_index: int) -> bool:
        first_left, first_top, first_mask = masks[first_index]
        second_left, second_top, second_mask = masks[second_index]
        left = max(first_left, second_left)
        top = max(first_top, second_top)
        right = min(first_left + first_mask.width, second_left + second_mask.width)
        bottom = min(first_top + first_mask.height, second_top + second_mask.height)
        if right <= left or bottom <= top:
            return False
        first_crop = first_mask.crop(
            (left - first_left, top - first_top, right - first_left, bottom - first_top)
        )
        second_crop = second_mask.crop(
            (
                left - second_left,
                top - second_top,
                right - second_left,
                bottom - second_top,
            )
        )
        return ImageChops.multiply(first_crop, second_crop).getbbox() is not None

    for index, first in enumerate(boxes):
        for second_index in range(index + 1, len(boxes)):
            second = boxes[second_index]
            gap_x = max(0, first[0] - second[2], second[0] - first[2])
            gap_y = max(0, first[1] - second[3], second[1] - first[3])
            if math.hypot(gap_x, gap_y) > proximity * 2 or masks_intersect(
                index, second_index
            ):
                continue
            first_center = ((first[0] + first[2]) / 2, (first[1] + first[3]) / 2)
            second_center = ((second[0] + second[2]) / 2, (second[1] + second[3]) / 2)
            bridge_draw.line(
                (*first_center, *second_center),
                fill=255,
                width=max(7, proximity),
            )
    return combined


def group_damage_masks(
    masks: list[tuple[int, int, Image.Image]],
    *,
    proximity: int = 24,
) -> list[list[tuple[int, int, Image.Image]]]:
    """Group overlapping or nearby tears without joining distant damage."""
    if not masks:
        return []

    def bounds(item: tuple[int, int, Image.Image]) -> tuple[int, int, int, int]:
        left, top, mask = item
        local = mask.getbbox() or (0, 0, mask.width, mask.height)
        return (
            left + local[0],
            top + local[1],
            left + local[2],
            top + local[3],
        )

    def nearby(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
        gap_x = max(0, first[0] - second[2], second[0] - first[2])
        gap_y = max(0, first[1] - second[3], second[1] - first[3])
        return math.hypot(gap_x, gap_y) <= proximity * 2

    groups: list[list[tuple[int, int, Image.Image]]] = []
    for item in masks:
        touching = [
            index
            for index, group in enumerate(groups)
            if any(nearby(bounds(item), bounds(existing)) for existing in group)
        ]
        if not touching:
            groups.append([item])
            continue
        target = touching[0]
        groups[target].append(item)
        for index in reversed(touching[1:]):
            groups[target].extend(groups.pop(index))
    return groups


def build_damage_layer(
    pcb_backplane: Image.Image,
    merged_mask: Image.Image,
    seed: int,
) -> Image.Image:
    """Reveal one fixed PCB backplane through a merged window-level tear mask."""
    width, height = merged_mask.size
    pcb = pcb_backplane.convert("RGB")
    if pcb.size != (width, height):
        pcb = cover_image(pcb, (width, height))

    inner = merged_mask.convert("L")
    pcb_layer = pcb.convert("RGBA")
    pcb_layer.putalpha(inner)
    return pcb_layer


def build_damage_patch(
    surface: Image.Image,
    pcb: Image.Image,
    seed: int,
) -> Image.Image:
    """Backward-compatible local damage renderer used by previews and tests."""
    width, height = surface.size
    inner = build_tear_mask((width, height), seed)
    return build_damage_layer(cover_image(pcb, (width, height)), inner, seed)


def harden_alpha(image: Image.Image, threshold: int = 44) -> Image.Image:
    """Avoid magenta color-key fringes on Win32 Tk layered windows."""
    result = image.copy()
    alpha = result.getchannel("A").point(
        lambda value: 255 if value >= threshold else 0
    )
    result.putalpha(alpha)
    return result


def catmull_rom_points(
    points: tuple[Point, ...], samples_per_segment: int = 4
) -> tuple[Point, ...]:
    """Sample a Catmull-Rom curve so the physics chain renders as one smooth lash."""
    if len(points) < 2:
        return points
    samples = max(2, int(samples_per_segment))
    result: list[Point] = [points[0]]
    for index in range(len(points) - 1):
        p0 = points[index - 1] if index > 0 else points[index]
        p1 = points[index]
        p2 = points[index + 1]
        p3 = points[index + 2] if index + 2 < len(points) else p2
        for sample in range(1, samples + 1):
            t = sample / samples
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                2 * p1[0]
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                2 * p1[1]
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            result.append((x, y))
    return tuple(result)


def movement_exceeds_drag_threshold(
    start: Point, current: Point, threshold: float = 6.0
) -> bool:
    return math.hypot(current[0] - start[0], current[1] - start[1]) >= threshold


def parking_position_from_origin(
    rectangle: WindowRectangle, origin: Point, handle: Point
) -> WhipParkingPosition:
    if rectangle.width <= 0 or rectangle.height <= 0:
        return WhipParkingPosition(1.0, 0.0)
    return WhipParkingPosition(
        (origin[0] + handle[0] - rectangle.left) / rectangle.width,
        (origin[1] + handle[1] - rectangle.top) / rectangle.height,
    ).normalized()


def origin_from_parking_position(
    rectangle: WindowRectangle,
    position: WhipParkingPosition,
    handle: Point,
) -> Point:
    normalized = position.normalized()
    return (
        rectangle.left + rectangle.width * normalized.horizontal - handle[0],
        rectangle.top + rectangle.height * normalized.vertical - handle[1],
    )


def sensor_origin_for_window(
    rectangle: WindowRectangle,
    parked_origin: Point,
    handle: Point,
    pose: SensorPose,
    *,
    movement_fraction: float = 1.0 / 3.0,
) -> Point:
    """Map IMU motion into a centered, bounded portion of the Codex window."""
    fraction = min(1.0, max(0.0, float(movement_fraction)))
    center_x = (rectangle.left + rectangle.right) / 2.0
    center_y = (rectangle.top + rectangle.bottom) / 2.0
    half_width = max(0.0, rectangle.width * fraction / 2.0)
    half_height = max(0.0, rectangle.height * fraction / 2.0)
    minimum_x = center_x - half_width
    maximum_x = center_x + half_width
    minimum_y = center_y - half_height
    maximum_y = center_y + half_height
    if maximum_x < minimum_x:
        minimum_x = maximum_x = (rectangle.left + rectangle.right) / 2.0
    if maximum_y < minimum_y:
        minimum_y = maximum_y = (rectangle.top + rectangle.bottom) / 2.0

    neutral_x = min(maximum_x, max(minimum_x, parked_origin[0] + handle[0]))
    neutral_y = min(maximum_y, max(minimum_y, parked_origin[1] + handle[1]))
    normalized_x = min(
        1.0,
        max(-1.0, pose.offset_x / SensorPoseTracker.MAX_OFFSET_X_PX),
    )
    normalized_y = min(
        1.0,
        max(-1.0, -pose.offset_y / SensorPoseTracker.MAX_OFFSET_Y_PX),
    )

    handle_x = neutral_x + (
        (maximum_x - neutral_x) * normalized_x
        if normalized_x >= 0.0
        else (neutral_x - minimum_x) * normalized_x
    )
    handle_y = neutral_y + (
        (maximum_y - neutral_y) * normalized_y
        if normalized_y >= 0.0
        else (neutral_y - minimum_y) * normalized_y
    )
    return handle_x - handle[0], handle_y - handle[1]


def default_overlay_position_path() -> Path:
    return user_data_dir() / "overlay-position.json"


def load_parking_position(path: Path) -> WhipParkingPosition | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            return None
        return WhipParkingPosition(
            float(data["horizontal"]), float(data["vertical"])
        ).normalized()
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def save_parking_position(path: Path, position: WhipParkingPosition) -> None:
    normalized = position.normalized()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "horizontal": round(normalized.horizontal, 6),
                "vertical": round(normalized.vertical, 6),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def scale_pose(pose: WhipPose, anchor: Point, scale: float) -> WhipPose:
    def point(value: Point) -> Point:
        return (
            anchor[0] + (value[0] - anchor[0]) * scale,
            anchor[1] + (value[1] - anchor[1]) * scale,
        )

    return WhipPose(
        handle_start=point(pose.handle_start),
        handle_end=point(pose.handle_end),
        cord=tuple(point(value) for value in pose.cord),
    )


def transform_whip_pose(
    pose: WhipPose,
    handle_anchor: Point,
    angle_degrees: float,
) -> WhipPose:
    """Rotate a pose around its handle start, then place it at an anchor."""
    radians = math.radians(float(angle_degrees))
    cosine = math.cos(radians)
    sine = math.sin(radians)
    source_x, source_y = pose.handle_start
    target_x, target_y = handle_anchor

    def point(value: Point) -> Point:
        relative_x = value[0] - source_x
        relative_y = value[1] - source_y
        return (
            target_x + relative_x * cosine - relative_y * sine,
            target_y + relative_x * sine + relative_y * cosine,
        )

    return WhipPose(
        handle_start=point(pose.handle_start),
        handle_end=point(pose.handle_end),
        cord=tuple(point(value) for value in pose.cord),
    )


def fit_pose_tip_to_window(
    pose: WhipPose,
    visual_origin: Point,
    rectangle: WindowRectangle,
    *,
    padding: float = 28.0,
) -> WhipPose:
    """Retarget a strike tip into Codex while keeping its live handle anchor."""
    usable_x = min(padding, max(0.0, (rectangle.width - 1) / 2.0))
    usable_y = min(padding, max(0.0, (rectangle.height - 1) / 2.0))
    raw_tip_x = visual_origin[0] + pose.cord[-1][0]
    raw_tip_y = visual_origin[1] + pose.cord[-1][1]
    target_screen_x = min(
        rectangle.right - usable_x,
        max(rectangle.left + usable_x, raw_tip_x),
    )
    target_screen_y = min(
        rectangle.bottom - usable_y,
        max(rectangle.top + usable_y, raw_tip_y),
    )
    target_tip = (
        target_screen_x - visual_origin[0],
        target_screen_y - visual_origin[1],
    )
    anchor = pose.handle_start
    source_vector = (
        pose.cord[-1][0] - anchor[0],
        pose.cord[-1][1] - anchor[1],
    )
    target_vector = (
        target_tip[0] - anchor[0],
        target_tip[1] - anchor[1],
    )
    source_length = math.hypot(*source_vector)
    target_length = math.hypot(*target_vector)
    if source_length <= 0.0001 or target_length <= 0.0001:
        return pose
    scale = target_length / source_length
    rotation = math.atan2(target_vector[1], target_vector[0]) - math.atan2(
        source_vector[1], source_vector[0]
    )
    cosine = math.cos(rotation)
    sine = math.sin(rotation)

    def point(value: Point) -> Point:
        relative_x = (value[0] - anchor[0]) * scale
        relative_y = (value[1] - anchor[1]) * scale
        return (
            anchor[0] + relative_x * cosine - relative_y * sine,
            anchor[1] + relative_x * sine + relative_y * cosine,
        )

    return WhipPose(
        handle_start=anchor,
        handle_end=point(pose.handle_end),
        cord=tuple(point(value) for value in pose.cord),
    )


def _resample_whip_pose(pose: WhipPose, cord_point_count: int) -> WhipPose:
    """Resample a cord by arc length so phase interpolation keeps its live shape."""
    if cord_point_count < 2:
        raise ValueError("whip poses require at least two cord points")
    if len(pose.cord) == cord_point_count:
        return pose

    segment_lengths = [
        math.dist(first, second)
        for first, second in zip(pose.cord, pose.cord[1:])
    ]
    total_length = sum(segment_lengths)
    if total_length <= 0.0:
        cord = tuple(pose.cord[0] for _ in range(cord_point_count))
        return WhipPose(pose.handle_start, pose.handle_end, cord)

    cumulative = [0.0]
    for length in segment_lengths:
        cumulative.append(cumulative[-1] + length)

    cord: list[Point] = []
    segment_index = 0
    for index in range(cord_point_count):
        target = total_length * index / (cord_point_count - 1)
        while (
            segment_index < len(segment_lengths) - 1
            and cumulative[segment_index + 1] < target
        ):
            segment_index += 1
        first = pose.cord[segment_index]
        second = pose.cord[segment_index + 1]
        segment_length = segment_lengths[segment_index]
        amount = (
            (target - cumulative[segment_index]) / segment_length
            if segment_length > 0.0
            else 0.0
        )
        cord.append(
            (
                first[0] + (second[0] - first[0]) * amount,
                first[1] + (second[1] - first[1]) * amount,
            )
        )
    return WhipPose(pose.handle_start, pose.handle_end, tuple(cord))


def _cubic_coordinate(value: float, first: float, second: float) -> float:
    inverse = 1.0 - value
    return (
        3.0 * inverse * inverse * value * first
        + 3.0 * inverse * value * value * second
        + value * value * value
    )


def cubic_bezier_ease_in_out(progress: float) -> float:
    """Evaluate cubic-bezier(0.77, 0, 0.175, 1) by its x coordinate."""
    target = min(1.0, max(0.0, progress))
    low = 0.0
    high = 1.0
    parameter = target
    for _ in range(18):
        x_value = _cubic_coordinate(parameter, 0.77, 0.175)
        if x_value < target:
            low = parameter
        else:
            high = parameter
        parameter = (low + high) / 2.0
    return _cubic_coordinate(parameter, 0.0, 1.0)


def interpolate_pose(start: WhipPose, end: WhipPose, progress: float) -> WhipPose:
    if len(start.cord) != len(end.cord):
        raise ValueError("whip poses must have the same number of cord points")
    amount = min(1.0, max(0.0, progress))

    def point(first: Point, second: Point) -> Point:
        return (
            first[0] + (second[0] - first[0]) * amount,
            first[1] + (second[1] - first[1]) * amount,
        )

    return WhipPose(
        handle_start=point(start.handle_start, end.handle_start),
        handle_end=point(start.handle_end, end.handle_end),
        cord=tuple(point(first, second) for first, second in zip(start.cord, end.cord)),
    )


class ManualWhipStroke:
    """A complete cord stroke from the live pose, with the tip at the click.

    The handle and every cord point participate; the impact is not a detached
    spark drawn underneath the handle. Idle/mouse motion still uses the solver.
    """

    IMPACT_SECONDS = 0.155
    DURATION_SECONDS = 0.280

    def __init__(self, physics: CartoonWhipPhysics, impact: Point) -> None:
        self.start = physics.pose()
        self.impact = impact
        length = physics.handle_length
        rope_length = sum(physics._segment_length(i) for i in range(physics.SEGMENTS - 1))

        def phase(source: WhipPose, anchor: Point) -> WhipPose:
            angle = math.atan2(source.handle_end[1] - source.handle_start[1],
                               source.handle_end[0] - source.handle_start[0])
            end = (anchor[0] + math.cos(angle)*length, anchor[1] + math.sin(angle)*length)
            source_length = sum(math.dist(a, b) for a, b in zip(source.cord, source.cord[1:]))
            cord = tuple((end[0] + (x-source.handle_end[0])*rope_length/source_length,
                          end[1] + (y-source.handle_end[1])*rope_length/source_length)
                         for x, y in source.cord)
            return _resample_whip_pose(WhipPose(anchor, end, cord), physics.SEGMENTS)

        local_strike = phase(CodexWhipEffects.STRIKE, (0.0, 0.0))
        anchor = (impact[0] - local_strike.cord[-1][0], impact[1] - local_strike.cord[-1][1])
        self.strike = phase(CodexWhipEffects.STRIKE, anchor)
        self.windup = phase(CodexWhipEffects.WINDUP, anchor)
        self.recoil = phase(CodexWhipEffects.RECOIL,
                            ((anchor[0]+impact[0])/2, (anchor[1]+impact[1])/2))
        self.settle = phase(CodexWhipEffects.SETTLE, impact)

    def sample(self, elapsed: float, cursor: Point) -> WhipPose:
        if elapsed <= 0:
            return self.start
        phases = ((0.0, 0.065, self.start, self.windup),
                  (0.065, self.IMPACT_SECONDS, self.windup, self.strike),
                  (self.IMPACT_SECONDS, 0.215, self.strike, self.recoil),
                  (0.215, self.DURATION_SECONDS, self.recoil, self.settle))
        begin, end, first, last = next((p for p in phases if elapsed <= p[1]), phases[-1])
        progress = cubic_bezier_ease_in_out((elapsed - begin)/(end - begin))
        pose = interpolate_pose(first, last, progress)
        # Angular interpolation keeps the black rod rigid during windup.
        first_angle = math.atan2(first.handle_end[1]-first.handle_start[1], first.handle_end[0]-first.handle_start[0])
        last_angle = math.atan2(last.handle_end[1]-last.handle_start[1], last.handle_end[0]-last.handle_start[0])
        angle = first_angle + CartoonWhipPhysics._wrap_angle(last_angle-first_angle)*progress
        length = math.dist(first.handle_start, first.handle_end)
        handle_end = (pose.handle_start[0]+math.cos(angle)*length, pose.handle_start[1]+math.sin(angle)*length)
        delta = (handle_end[0]-pose.handle_end[0], handle_end[1]-pose.handle_end[1])
        recovery = cubic_bezier_ease_in_out((elapsed-self.IMPACT_SECONDS)/(self.DURATION_SECONDS-self.IMPACT_SECONDS))
        translation = ((cursor[0]-self.impact[0])*recovery, (cursor[1]-self.impact[1])*recovery)
        cord = tuple((x+delta[0]*(1-i/(len(pose.cord)-1))**3+translation[0],
                      y+delta[1]*(1-i/(len(pose.cord)-1))**3+translation[1])
                     for i, (x,y) in enumerate(pose.cord))
        return WhipPose((pose.handle_start[0]+translation[0], pose.handle_start[1]+translation[1]),
                        (handle_end[0]+translation[0], handle_end[1]+translation[1]), cord)


def shake_offset(progress: float, amplitude: float = 22.0) -> tuple[int, int]:
    value = min(1.0, max(0.0, progress))
    if value in {0.0, 1.0}:
        return 0, 0
    decay = (1.0 - value) ** 1.6
    return (
        round(-math.sin(value * math.pi * 8.0) * amplitude * decay),
        round(math.sin(value * math.pi * 10.0) * amplitude * 0.45 * decay),
    )


def scare_eye_openness(
    elapsed_ms: float,
    blackout_ms: int = 2000,
    eyes_ms: int = 3000,
) -> float:
    """Return vertical eye openness for two line flashes, reveal, then hold."""
    elapsed = float(elapsed_ms)
    blackout = max(0.0, float(blackout_ms))
    steady_duration = max(1.0, float(eyes_ms))
    flash_on_ms = 140.0
    flash_off_ms = 110.0
    flash_cycle_ms = flash_on_ms + flash_off_ms
    flash_duration = flash_cycle_ms * 2.0
    opening_duration = 320.0
    line_openness = 0.018
    total_duration = blackout + flash_duration + opening_duration + steady_duration
    if elapsed < blackout or elapsed >= total_duration:
        return 0.0
    phase = elapsed - blackout
    if phase < flash_duration:
        return line_openness if phase % flash_cycle_ms < flash_on_ms else 0.0
    opening_phase = phase - flash_duration
    if opening_phase < opening_duration:
        progress = cubic_bezier_ease_in_out(opening_phase / opening_duration)
        return line_openness + (1.0 - line_openness) * progress
    return 1.0


def scare_animation_total_ms(
    blackout_ms: int,
    eyes_ms: int,
) -> int:
    return int(blackout_ms) + 500 + 320 + int(eyes_ms)


def build_whip_crack_wav(
    sample_rate: int = 44100, duration_ms: int = 220
) -> bytes:
    """Build a short deterministic crack without shipping an external asset."""
    frame_count = round(sample_rate * duration_ms / 1000)
    random_source = random.Random(0xC0DE)
    previous_noise = 0.0
    frames = bytearray()

    for index in range(frame_count):
        elapsed = index / sample_rate
        noise = random_source.uniform(-1.0, 1.0)
        high_noise = noise - previous_noise * 0.82
        previous_noise = noise

        attack = min(1.0, elapsed / 0.0012)
        crack = high_noise * attack * math.exp(-elapsed * 68.0)
        echo_elapsed = elapsed - 0.034
        echo = (
            high_noise * 0.28 * math.exp(-echo_elapsed * 42.0)
            if echo_elapsed >= 0.0
            else 0.0
        )
        thump = (
            math.sin(2.0 * math.pi * 92.0 * elapsed)
            * 0.26
            * math.exp(-elapsed * 24.0)
        )
        air = (
            math.sin(2.0 * math.pi * 760.0 * elapsed)
            * 0.10
            * math.exp(-elapsed * 31.0)
        )
        value = math.tanh((crack + echo + thump + air) * 1.35)
        sample = round(max(-1.0, min(1.0, value)) * 32767)
        frames.extend(sample.to_bytes(2, "little", signed=True))

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(frames)
    return buffer.getvalue()


def bundled_asset_path(relative_path: str) -> Path:
    """Resolve assets both from source and a PyInstaller one-file bundle."""
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS"))
    else:
        root = Path(__file__).resolve().parents[2]
    return root / "assets" / relative_path


def load_whip_strike_wav() -> bytes:
    """Load the licensed layered recording, retaining a generated fallback."""
    try:
        sound = bundled_asset_path("audio/whip_strike.wav").read_bytes()
        if len(sound) < 44 or sound[:4] != b"RIFF" or sound[8:12] != b"WAVE":
            raise ValueError("invalid WAV asset")
        return sound
    except (OSError, ValueError):
        return build_whip_crack_wav()


def play_whip_crack(sound: bytes) -> bool:
    if os.name == "nt":
        import winsound

        def play() -> None:
            try:
                # SND_MEMORY is synchronous, so keep it off Tk's UI thread.
                winsound.PlaySound(sound, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
            except RuntimeError:
                return

        threading.Thread(target=play, name="codex-whip-sound", daemon=True).start()
        return True
    if sys.platform == "darwin":
        from .macos_api import play_wav

        return play_wav(sound)
    return False


if os.name == "nt":
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]


    class CURSOR_POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(RECT))
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = (wintypes.HWND,)
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.IsWindowVisible.argtypes = (wintypes.HWND,)
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsIconic.argtypes = (wintypes.HWND,)
    _user32.IsIconic.restype = wintypes.BOOL
    _user32.IsZoomed.argtypes = (wintypes.HWND,)
    _user32.IsZoomed.restype = wintypes.BOOL
    _user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    _user32.GetAncestor.restype = wintypes.HWND
    _user32.GetCursorPos.argtypes = (ctypes.POINTER(CURSOR_POINT),)
    _user32.GetCursorPos.restype = wintypes.BOOL
    _user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    _user32.GetAsyncKeyState.restype = wintypes.SHORT
    _user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
    _user32.GetSystemMetrics.restype = ctypes.c_int
    _user32.SetWindowPos.argtypes = (
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.SetLayeredWindowAttributes.argtypes = (
        wintypes.HWND,
        wintypes.COLORREF,
        wintypes.BYTE,
        wintypes.DWORD,
    )
    _user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    _user32.SystemParametersInfoW.argtypes = (
        wintypes.UINT,
        wintypes.UINT,
        wintypes.LPVOID,
        wintypes.UINT,
    )
    _user32.SystemParametersInfoW.restype = wintypes.BOOL

    if ctypes.sizeof(ctypes.c_void_p) == 8:
        _get_window_long = _user32.GetWindowLongPtrW
        _set_window_long = _user32.SetWindowLongPtrW
    else:
        _get_window_long = _user32.GetWindowLongW
        _set_window_long = _user32.SetWindowLongW
    _get_window_long.argtypes = (wintypes.HWND, ctypes.c_int)
    _get_window_long.restype = ctypes.c_ssize_t
    _set_window_long.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
    _set_window_long.restype = ctypes.c_ssize_t


GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOSENDCHANGING = 0x0400
SPI_GETCLIENTAREAANIMATION = 0x1042
GA_ROOT = 2
VK_ESCAPE = 0x1B
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def _window_rectangle(hwnd: int) -> WindowRectangle | None:
    if os.name == "nt":
        if not _user32.IsWindow(hwnd):
            return None
        value = RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(value)):
            return None
        return WindowRectangle(value.left, value.top, value.right, value.bottom)
    if sys.platform == "darwin":
        try:
            from .macos_api import window_for_pid

            value = window_for_pid(hwnd)
            if value is not None:
                return WindowRectangle(
                    value.left,
                    value.top,
                    value.left + value.width,
                    value.top + value.height,
                )
        except Exception:
            return None
    return None


def _cursor_position() -> tuple[int, int] | None:
    if os.name == "nt":
        value = CURSOR_POINT()
        if not _user32.GetCursorPos(ctypes.byref(value)):
            return None
        return int(value.x), int(value.y)
    if sys.platform == "darwin":
        try:
            from .macos_api import cursor_position

            return cursor_position()
        except Exception:
            return None
    return None


def _virtual_screen_rectangle() -> WindowRectangle:
    if os.name == "nt":
        left = int(_user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        top = int(_user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = max(1, int(_user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)))
        height = max(1, int(_user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)))
        return WindowRectangle(left, top, left + width, top + height)
    if sys.platform == "darwin":
        try:
            from .macos_api import virtual_screen_rectangle

            return WindowRectangle(*virtual_screen_rectangle())
        except Exception:
            pass
    return WindowRectangle(0, 0, 1, 1)


def _client_animations_enabled() -> bool:
    if sys.platform == "darwin":
        try:
            from .macos_api import animations_enabled

            return animations_enabled()
        except Exception:
            return True
    if os.name != "nt":
        return True
    enabled = wintypes.BOOL(True)
    if not _user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0
    ):
        return True
    return bool(enabled.value)


def _target_exists(hwnd: int) -> bool:
    if os.name == "nt":
        return bool(_user32.IsWindow(hwnd))
    if sys.platform == "darwin":
        try:
            from .macos_api import window_is_available

            return window_is_available(hwnd)
        except Exception:
            return False
    return False


def _target_is_fullscreen(hwnd: int) -> bool:
    if os.name == "nt":
        return bool(_user32.IsZoomed(hwnd))
    if sys.platform == "darwin":
        try:
            from .macos_api import window_is_fullscreen

            return window_is_fullscreen(hwnd)
        except Exception:
            return False
    return False


def _move_target_window(hwnd: int, left: int, top: int) -> bool:
    if os.name == "nt":
        return bool(
            _user32.SetWindowPos(
                hwnd,
                0,
                left,
                top,
                0,
                0,
                SWP_NOSIZE
                | SWP_NOZORDER
                | SWP_NOACTIVATE
                | SWP_NOSENDCHANGING,
            )
        )
    if sys.platform == "darwin":
        try:
            from .macos_api import move_window

            return move_window(hwnd, left, top)
        except Exception:
            return False
    return False


def _escape_pressed() -> bool:
    if os.name == "nt":
        return bool(_user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
    if sys.platform == "darwin":
        try:
            from .macos_api import escape_pressed

            return escape_pressed()
        except Exception:
            return False
    return False


class CodexWhipEffects:
    WIDTH = 1200
    HEIGHT = 1200
    FRAME_MS = 16
    WHIP_DURATION_MS = 280
    IMPACT_AT_MS = 155
    SHAKE_DURATION_MS = 220
    MANUAL_DEBOUNCE_MS = 350
    DRAG_THRESHOLD = 6.0
    SYNC_INTERVAL_MS = 16
    SENSOR_RETURN_DELAY_SECONDS = 3.0
    SENSOR_MOTION_GESTURE_GAP_SECONDS = 0.35
    TRANSPARENT = "#ff00ff"
    INPUT_BACKGROUND = "#010101"

    IDLE = extend_whip_pose(
        WhipPose(
            (408.0, 46.0),
            (332.0, 116.0),
            ((332.0, 116.0), (286.0, 130.0), (240.0, 112.0),
             (198.0, 78.0), (154.0, 68.0), (114.0, 94.0), (82.0, 130.0)),
        ),
        1.50,
        x_offset=190.0,
        y_offset=550.0,
    )
    WINDUP = extend_whip_pose(
        WhipPose(
            (408.0, 46.0),
            (372.0, 24.0),
            ((372.0, 24.0), (330.0, 8.0), (282.0, 18.0),
             (236.0, 48.0), (194.0, 88.0), (146.0, 106.0), (98.0, 86.0)),
        ),
        1.50,
        x_offset=190.0,
        y_offset=550.0,
    )
    STRIKE = extend_whip_pose(
        WhipPose(
            (408.0, 46.0),
            (326.0, 150.0),
            ((326.0, 150.0), (302.0, 198.0), (266.0, 242.0),
             (218.0, 286.0), (162.0, 326.0), (104.0, 354.0), (48.0, 372.0)),
        ),
        1.50,
        x_offset=190.0,
        y_offset=550.0,
    )
    RECOIL = extend_whip_pose(
        WhipPose(
            (406.0, 44.0),
            (340.0, 126.0),
            ((340.0, 126.0), (306.0, 178.0), (266.0, 216.0),
             (218.0, 232.0), (168.0, 212.0), (120.0, 174.0), (78.0, 146.0)),
        ),
        1.50,
        x_offset=190.0,
        y_offset=550.0,
    )
    SETTLE = extend_whip_pose(
        WhipPose(
            (406.0, 44.0),
            (326.0, 126.0),
            ((326.0, 126.0), (288.0, 150.0), (246.0, 162.0),
             (202.0, 146.0), (160.0, 118.0), (118.0, 116.0), (84.0, 138.0)),
        ),
        1.50,
        x_offset=190.0,
        y_offset=550.0,
    )
    CURSOR = scale_pose(IDLE, IDLE.handle_start, 0.52)

    def __init__(
        self,
        root: tk.Tk,
        log: LogHandler,
        play_sound: SoundHandler | None = None,
        manual_whip: ManualWhipHandler | None = None,
        position_path: Path | None = None,
        damage_interval: int = 1,
    ) -> None:
        self._root = root
        self._log = log
        self._target_hwnd: int | None = None
        self._animation_started_at = 0.0
        self._impact_fired = False
        self._animation_after: str | None = None
        self._physics_after: str | None = None
        self._physics: CartoonWhipPhysics | None = None
        self._physics_last_time = 0.0
        self._sensor_physics: CartoonWhipPhysics | None = None
        self._sensor_physics_last_time = 0.0
        self._manual_target: Point | None = None
        self._manual_motion_direction: Point = (1.0, 0.0)
        self._manual_strike_started_at: float | None = None
        self._manual_strike_impact: Point | None = None
        self._manual_strike_fired = False
        self._manual_stroke: ManualWhipStroke | None = None
        self._pending_impact_screen: Point | None = None
        self._pending_damage_direction: Point = (1.0, 0.0)
        self._sync_after: str | None = None
        self._shake_after: str | None = None
        self._shake_started_at = 0.0
        self._shake_base: WindowRectangle | None = None
        self._shake_hwnd: int | None = None
        self._current_pose = self.IDLE
        self._animation_start_pose = self.IDLE
        self._animation_windup_pose = self.WINDUP
        self._animation_strike_pose = self.STRIKE
        self._animation_recoil_pose = self.RECOIL
        self._animation_settle_pose = self.SETTLE
        self._animation_idle_pose = self.IDLE
        self._animations_enabled = _client_animations_enabled()
        self._crack_sound = load_whip_strike_wav()
        self.sound_enabled = True
        self.wounds_enabled = True
        sound_handler = play_sound or play_whip_crack
        self._play_sound = lambda sound: sound_handler(sound) if self.sound_enabled else False
        self._manual_whip = manual_whip
        self._manual_armed = False
        self._manual_last_strike_at = 0.0
        self._drag_start: Point | None = None
        self._drag_origin: Point | None = None
        self._dragging = False
        self._visual_origin: Point = (0.0, 0.0)
        self._position_path = position_path or default_overlay_position_path()
        self._parking_position = load_parking_position(self._position_path)
        self._damage_random = random.Random()
        self._damage_items: list[HealingDamage] = []
        self._damage_photo_refs: list[ImageTk.PhotoImage] = []
        self._damage_render_key: tuple[object, ...] | None = None
        self._last_damage_strike_at = 0.0
        self._damage_edge_seed = self._damage_random.randrange(1, 2**31)
        self._damage_interval = 1
        self._damage_strike_count = 0
        self.set_damage_interval(damage_interval)
        self._scare_active = False
        self._scare_started_at = 0.0
        self._scare_blackout_ms = 2000
        self._scare_eyes_ms = 3000
        self._scare_after: str | None = None
        self._scare_photo: ImageTk.PhotoImage | None = None
        self._scare_base_image: Image.Image | None = None
        self._scare_base_size = (0, 0)
        self._pcb_image = Image.open(
            bundled_asset_path("visual/pcb-photoreal-v1.png")
        ).convert("RGB")
        scare_source = Image.open(
            bundled_asset_path("visual/scare-eyes-red-v1.png")
        ).convert("RGB")
        bright = scare_source.convert("L").point(
            lambda value: 255 if value > 18 else 0
        )
        bounds = bright.getbbox()
        if bounds is not None:
            margin = 26
            scare_source = scare_source.crop(
                (
                    max(0, bounds[0] - margin),
                    max(0, bounds[1] - margin),
                    min(scare_source.width, bounds[2] + margin),
                    min(scare_source.height, bounds[3] + margin),
                )
            )
        self._scare_eyes_source = scare_source
        self._pcb_backplane: Image.Image | None = None
        self._pcb_backplane_size = (0, 0)
        self._sensor_pose_target = SensorPose(0.0, 0.0, 0.0, 0.0)
        self._sensor_pose_current = SensorPose(0.0, 0.0, 0.0, 0.0)
        self._sensor_pose_updated_at = 0.0
        self._last_sensor_motion_at = 0.0
        self._sensor_motion_point: Point | None = None
        self._sensor_motion_xx = 0.0
        self._sensor_motion_xy = 0.0
        self._sensor_motion_yy = 0.0
        self._sensor_motion_direction: Point = (1.0, 0.0)

        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.title("Codex Whip Overlay")
        self.window.overrideredirect(True)
        self.window.configure(bg=self.TRANSPARENT)
        self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}+0+0")
        self.window.attributes("-topmost", True)
        if os.name == "nt":
            self.window.attributes("-transparentcolor", self.TRANSPARENT)

        self.canvas = tk.Canvas(
            self.window,
            width=self.WIDTH,
            height=self.HEIGHT,
            bg=self.TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)
        if sys.platform == "darwin":
            self._enable_macos_transparency(self.window, self.canvas)
        from .whip_drawing import WhipDrawing
        self._whip_drawing = WhipDrawing(self.canvas)
        self._impact_items = tuple(
            self.canvas.create_line(
                0, 0, 0, 0, fill="#F1B84B", width=3, capstyle=tk.ROUND,
                state="hidden"
            )
            for _ in range(8)
        )
        self._impact_ring = self.canvas.create_oval(
            0, 0, 0, 0, outline="#F1B84B", width=3, state="hidden"
        )
        self._draw_pose(self.IDLE)
        self.window.update_idletasks()
        self._overlay_hwnd = (
            int(_user32.GetAncestor(self.window.winfo_id(), GA_ROOT))
            if os.name == "nt"
            else int(self.window.winfo_id())
        )
        self._make_click_through()

        self._create_damage_overlay()
        self._create_scare_overlay()

        self.hit_window = self._create_input_window(
            "Codex Whip Handle", cursor="hand2"
        )
        self.capture_window = self._create_input_window(
            "Codex Whip Mouse Capture", cursor="none"
        )
        self.hit_window.bind("<ButtonPress-1>", self._handle_press)
        self.hit_window.bind("<B1-Motion>", self._handle_drag)
        self.hit_window.bind("<ButtonRelease-1>", self._handle_release)
        self.hit_window.bind("<Button-3>", self._handle_clock_toggle)
        self.capture_window.bind("<Motion>", self._handle_manual_motion)
        self.capture_window.bind("<Button-1>", self._handle_manual_strike)
        self.capture_window.bind("<Button-3>", self._handle_manual_cancel)
        self._schedule_sync()

    def _make_click_through(self) -> None:
        if sys.platform == "darwin":
            from .macos_api import configure_tk_window

            configure_tk_window(
                str(self.window.title()),
                click_through=True,
                transparent=True,
            )
            return
        if os.name != "nt":
            return
        hwnd = self._overlay_hwnd
        styles = int(_get_window_long(hwnd, GWL_EXSTYLE))
        styles |= (
            WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        )
        _set_window_long(hwnd, GWL_EXSTYLE, styles)
        _user32.SetLayeredWindowAttributes(hwnd, 0x00FF00FF, 255, LWA_COLORKEY)
        _user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _create_damage_overlay(self) -> None:
        self.damage_window = tk.Toplevel(self._root)
        self.damage_window.withdraw()
        self.damage_window.title("Codex Whip Damage")
        self.damage_window.overrideredirect(True)
        self.damage_window.configure(bg=self.TRANSPARENT)
        self.damage_window.attributes("-topmost", True)
        if os.name == "nt":
            self.damage_window.attributes("-transparentcolor", self.TRANSPARENT)
        self.damage_canvas = tk.Canvas(
            self.damage_window,
            bg=self.TRANSPARENT,
            highlightthickness=0,
            bd=0,
        )
        self.damage_canvas.pack(fill="both", expand=True)
        if sys.platform == "darwin":
            self._enable_macos_transparency(self.damage_window, self.damage_canvas)
        self.damage_window.update_idletasks()
        self._damage_hwnd = self._window_handle(self.damage_window)
        if os.name == "nt":
            styles = int(_get_window_long(self._damage_hwnd, GWL_EXSTYLE))
            styles |= (
                WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            )
            _set_window_long(self._damage_hwnd, GWL_EXSTYLE, styles)
            _user32.SetLayeredWindowAttributes(
                self._damage_hwnd, 0x00FF00FF, 255, LWA_COLORKEY
            )
        elif sys.platform == "darwin":
            from .macos_api import configure_tk_window

            configure_tk_window(
                str(self.damage_window.title()),
                click_through=True,
                transparent=True,
            )

    def _create_scare_overlay(self) -> None:
        self.scare_window = tk.Toplevel(self._root)
        self.scare_window.withdraw()
        self.scare_window.title("Codex Whip Red Eyes")
        self.scare_window.overrideredirect(True)
        self.scare_window.configure(bg="#000000")
        self.scare_window.attributes("-topmost", True)
        self.scare_canvas = tk.Canvas(
            self.scare_window,
            bg="#000000",
            highlightthickness=0,
            bd=0,
        )
        self.scare_canvas.pack(fill="both", expand=True)
        self.scare_window.update_idletasks()
        self._scare_hwnd = self._window_handle(self.scare_window)
        if os.name == "nt":
            styles = int(_get_window_long(self._scare_hwnd, GWL_EXSTYLE))
            styles |= (
                WS_EX_LAYERED
                | WS_EX_TRANSPARENT
                | WS_EX_TOOLWINDOW
                | WS_EX_NOACTIVATE
            )
            _set_window_long(self._scare_hwnd, GWL_EXSTYLE, styles)
            _user32.SetLayeredWindowAttributes(self._scare_hwnd, 0, 255, LWA_ALPHA)
        elif sys.platform == "darwin":
            from .macos_api import configure_tk_window

            configure_tk_window(
                str(self.scare_window.title()),
                click_through=True,
                transparent=False,
            )

    @staticmethod
    def _enable_macos_transparency(
        window: tk.Toplevel,
        canvas: tk.Canvas,
    ) -> None:
        try:
            window.configure(bg="systemTransparent")
            canvas.configure(bg="systemTransparent")
        except tk.TclError:
            try:
                window.attributes("-transparent", True)
            except tk.TclError:
                pass

    def _raise_scare(self) -> None:
        if sys.platform == "darwin":
            from .macos_api import raise_tk_window

            raise_tk_window(str(self.scare_window.title()))
            return
        if os.name != "nt":
            return
        _user32.SetWindowPos(
            self._scare_hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def set_scare_timing(self, blackout_ms: int, eyes_ms: int) -> None:
        blackout = int(blackout_ms)
        duration = int(eyes_ms)
        if not 200 <= blackout <= 5000:
            raise ValueError("黑屏时间必须在 200–5000 毫秒之间")
        if not 300 <= duration <= 10000:
            raise ValueError("红眼显示时间必须在 300–10000 毫秒之间")
        self._scare_blackout_ms = blackout
        self._scare_eyes_ms = duration

    def toggle_scare(self) -> bool:
        return False  # Retired: old callers cannot activate the effect.
        if self._scare_active:
            self._hide_scare()
            return True
        if not self._target_available():
            self._log("黑屏红眼跳过：当前没有可见的 Codex 窗口")
            return False
        rectangle = _window_rectangle(self._target_hwnd)
        if rectangle is None:
            return False
        self.disarm_manual(log=False)
        self._cancel_animation()
        self.window.withdraw()
        self.hit_window.withdraw()
        self.capture_window.withdraw()
        self.damage_window.withdraw()
        self.scare_canvas.delete("eyes")
        self._set_geometry(self.scare_window, rectangle)
        self.scare_window.deiconify()
        self._raise_scare()
        self._scare_active = True
        self._scare_started_at = time.perf_counter()
        self._scare_frame()
        self._log("黑屏红眼已触发：横线闪烁两次后睁眼；再次按组合键或 Esc 可关闭")
        return True

    def _hide_scare(self, *, restore: bool = True) -> None:
        self._scare_active = False
        if self._scare_after is not None:
            try:
                self._root.after_cancel(self._scare_after)
            except tk.TclError:
                pass
            self._scare_after = None
        self.scare_canvas.delete("eyes")
        self._scare_photo = None
        self.scare_window.withdraw()
        if restore and self._target_available():
            self._sync_position()
            self.window.deiconify()
            self._make_click_through()
            self._show_idle_hitbox()

    def _scare_frame(self) -> None:
        self._scare_after = None
        if not self._scare_active:
            return
        if not self._target_available():
            self._hide_scare(restore=False)
            return
        rectangle = _window_rectangle(self._target_hwnd)
        if rectangle is None:
            self._hide_scare(restore=False)
            return
        self._set_geometry(self.scare_window, rectangle)
        elapsed_ms = (time.perf_counter() - self._scare_started_at) * 1000.0
        total_ms = scare_animation_total_ms(
            self._scare_blackout_ms,
            self._scare_eyes_ms,
        )
        if elapsed_ms >= total_ms:
            self._hide_scare()
            return
        openness = scare_eye_openness(
            elapsed_ms,
            self._scare_blackout_ms,
            self._scare_eyes_ms,
        )
        self.scare_canvas.delete("eyes")
        if openness > 0.0:
            desired_width = max(180, round(rectangle.width * 0.62))
            desired_height = max(90, round(rectangle.height * 0.34))
            scale = min(
                desired_width / self._scare_eyes_source.width,
                desired_height / self._scare_eyes_source.height,
            )
            base_size = (
                max(1, round(self._scare_eyes_source.width * scale)),
                max(1, round(self._scare_eyes_source.height * scale)),
            )
            if self._scare_base_image is None or self._scare_base_size != base_size:
                self._scare_base_image = self._scare_eyes_source.resize(
                    base_size, Image.Resampling.LANCZOS
                )
                self._scare_base_size = base_size
            width = base_size[0]
            height = max(2, round(base_size[1] * openness))
            frame = self._scare_base_image.resize(
                (width, height), Image.Resampling.LANCZOS
            )
            self._scare_photo = ImageTk.PhotoImage(frame)
            self.scare_canvas.create_image(
                rectangle.width // 2,
                round(rectangle.height * 0.48),
                image=self._scare_photo,
                anchor=tk.CENTER,
                tags="eyes",
            )
        self._raise_scare()
        self._scare_after = self._root.after(self.FRAME_MS, self._scare_frame)

    def _sync_damage_overlay(self, rectangle: WindowRectangle) -> None:
        self._set_geometry(self.damage_window, rectangle)
        if not self.damage_window.winfo_viewable():
            self.damage_window.deiconify()
        self._draw_damage_overlay(rectangle.width, rectangle.height)
        if os.name == "nt":
            _user32.SetWindowPos(
                self._damage_hwnd,
                HWND_TOPMOST,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            )
        elif sys.platform == "darwin":
            from .macos_api import raise_tk_window

            raise_tk_window(str(self.damage_window.title()))
        self._raise_visual()

    def _record_damage(
        self,
        screen_point: Point,
        direction: Point = (1.0, 0.0),
    ) -> None:
        if self._target_hwnd is None:
            return
        rectangle = _window_rectangle(self._target_hwnd)
        if rectangle is None:
            return
        base_width = min(460, max(260, round(rectangle.width * 0.38)))
        base_height = min(180, max(112, round(rectangle.height * 0.20)))
        local_x = min(rectangle.width - 1, max(0, round(screen_point[0] - rectangle.left)))
        local_y = min(rectangle.height - 1, max(0, round(screen_point[1] - rectangle.top)))
        seed = self._damage_random.randrange(1, 2**31)
        mask = build_directional_tear_mask(
            (base_width, base_height),
            seed,
            direction,
        )
        unclipped_left = local_x - mask.width // 2
        unclipped_top = local_y - mask.height // 2
        source_left = max(0, -unclipped_left)
        source_top = max(0, -unclipped_top)
        source_right = min(mask.width, rectangle.width - unclipped_left)
        source_bottom = min(mask.height, rectangle.height - unclipped_top)
        if source_right - source_left < 80 or source_bottom - source_top < 50:
            return
        mask = mask.crop((source_left, source_top, source_right, source_bottom))
        left = max(0, unclipped_left)
        top = max(0, unclipped_top)
        now = time.perf_counter()
        newest = HealingDamage(left, top, mask, now, seed)
        self._damage_items.append(newest)
        self._last_damage_strike_at = now
        self._sync_damage_overlay(rectangle)

    def set_damage_interval(self, strikes_per_wound: int) -> None:
        value = int(strikes_per_wound)
        if not 0 <= value <= 100:
            raise ValueError("PCB 伤口间隔必须在 0–100 次之间")
        self._damage_interval = value
        self._damage_strike_count = 0

    def set_feedback(self, *, wounds_enabled: bool, sound_enabled: bool) -> None:
        self.wounds_enabled = wounds_enabled
        self.sound_enabled = sound_enabled
        if not wounds_enabled:
            self._damage_items.clear()
            self._damage_render_key = None
            self.damage_canvas.delete('all')
            self.damage_window.withdraw()

    def _maybe_record_damage(
        self,
        screen_point: Point,
        direction: Point = (1.0, 0.0),
    ) -> bool:
        """Reveal a wound on each configured Nth strike, deterministically."""
        if not getattr(self, 'wounds_enabled', True):
            return False
        interval = max(1, int(getattr(self, "_damage_interval", 1)))
        count = int(getattr(self, "_damage_strike_count", 0)) + 1
        self._damage_strike_count = count
        # Every strike postpones healing, including strikes that do not open a
        # new PCB tear under the configured visual frequency.
        self._last_damage_strike_at = time.perf_counter()
        if count % interval != 0:
            return False
        self._record_damage(screen_point, direction)
        return True

    def _draw_damage_overlay(self, width: int, height: int) -> None:
        now = time.perf_counter()
        quiet_seconds = (
            now - self._last_damage_strike_at
            if self._last_damage_strike_at > 0.0
            else float("inf")
        )
        active: list[HealingDamage] = []
        visible_masks: list[tuple[int, int, Image.Image]] = []
        render_parts: list[tuple[int, int, int]] = []
        for damage in self._damage_items:
            age = now - damage.created_at
            if quiet_seconds >= 3.85:
                continue
            active.append(damage)
            visibility = healing_visibility(age, quiet_seconds)
            if visibility <= 0.0:
                continue
            mask = damage.mask
            top = damage.top
            if self._animations_enabled:
                displayed_height = max(2, round(mask.height * visibility))
                top += (mask.height - displayed_height) // 2
                mask = mask.resize(
                    (mask.width, displayed_height), Image.Resampling.LANCZOS
                )
            visible_masks.append((damage.left, top, mask))
            render_parts.append((damage.seed, top, mask.height))
        self._damage_items = active
        render_key: tuple[object, ...] = (width, height, *render_parts)
        if render_key == self._damage_render_key:
            return
        self._damage_render_key = render_key
        self.damage_canvas.delete("damage")
        self._damage_photo_refs = []
        if not visible_masks:
            return

        if self._pcb_backplane is None or self._pcb_backplane_size != (width, height):
            self._pcb_backplane = cover_image(self._pcb_image, (width, height))
            self._pcb_backplane_size = (width, height)

        for group in group_damage_masks(visible_masks, proximity=24):
            margin = 30
            group_left = max(0, min(item[0] for item in group) - margin)
            group_top = max(0, min(item[1] for item in group) - margin)
            group_right = min(
                width,
                max(item[0] + item[2].width for item in group) + margin,
            )
            group_bottom = min(
                height,
                max(item[1] + item[2].height for item in group) + margin,
            )
            if group_right <= group_left or group_bottom <= group_top:
                continue
            local_masks = [
                (left - group_left, top - group_top, mask)
                for left, top, mask in group
            ]
            merged = merge_damage_masks(
                (group_right - group_left, group_bottom - group_top),
                local_masks,
                proximity=24,
            )
            pcb_crop = self._pcb_backplane.crop(
                (group_left, group_top, group_right, group_bottom)
            )
            image = build_damage_layer(pcb_crop, merged, self._damage_edge_seed)
            image = harden_alpha(image)
            photo = ImageTk.PhotoImage(image)
            self._damage_photo_refs.append(photo)
            self.damage_canvas.create_image(
                group_left,
                group_top,
                image=photo,
                anchor=tk.NW,
                tags="damage",
            )

    def _create_input_window(self, title: str, *, cursor: str) -> tk.Toplevel:
        window = tk.Toplevel(self._root)
        window.withdraw()
        window.title(title)
        window.overrideredirect(True)
        window.configure(bg=self.INPUT_BACKGROUND, cursor=cursor)
        window.geometry("1x1+0+0")
        window.attributes("-topmost", True)
        if os.name == "nt":
            # A non-zero alpha keeps the window hit-testable while remaining
            # visually imperceptible. It intentionally has no color key.
            window.attributes("-alpha", 0.01)
        elif sys.platform == "darwin":
            window.attributes("-alpha", 0.01)
        window.update_idletasks()
        self._make_input_window(window)
        return window

    @staticmethod
    def _window_handle(window: tk.Toplevel) -> int:
        if os.name != "nt":
            return int(window.winfo_id())
        return int(_user32.GetAncestor(window.winfo_id(), GA_ROOT))

    def _make_input_window(self, window: tk.Toplevel) -> None:
        if sys.platform == "darwin":
            from .macos_api import configure_tk_window

            configure_tk_window(
                str(window.title()),
                click_through=False,
                transparent=True,
                alpha=0.01,
            )
            return
        if os.name != "nt":
            return
        hwnd = self._window_handle(window)
        styles = int(_get_window_long(hwnd, GWL_EXSTYLE))
        styles |= WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        styles &= ~WS_EX_TRANSPARENT
        _set_window_long(hwnd, GWL_EXSTYLE, styles)
        _user32.SetLayeredWindowAttributes(hwnd, 0, 1, LWA_ALPHA)
        _user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _set_geometry(
        self, window: tk.Toplevel, rectangle: WindowRectangle
    ) -> None:
        width = max(1, rectangle.width)
        height = max(1, rectangle.height)
        if os.name != "nt":
            window.geometry(
                f"{width}x{height}{rectangle.left:+d}{rectangle.top:+d}"
            )
            if sys.platform == "darwin":
                try:
                    window.lift()
                except tk.TclError:
                    pass
            return
        _user32.SetWindowPos(
            self._window_handle(window),
            HWND_TOPMOST,
            rectangle.left,
            rectangle.top,
            width,
            height,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _raise_visual(self) -> None:
        if getattr(self, "_settings_open", False):
            return
        if sys.platform == "darwin":
            from .macos_api import raise_tk_window

            raise_tk_window(str(self.damage_window.title()))
            raise_tk_window(str(self.window.title()))
            return
        if os.name != "nt":
            return
        _user32.SetWindowPos(
            self._overlay_hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _sensor_motion_screen_point(self, pose: SensorPose) -> Point:
        target_hwnd = getattr(self, "_target_hwnd", None)
        rectangle = _window_rectangle(target_hwnd) if target_hwnd is not None else None
        if rectangle is not None:
            parked = self._parked_origin(rectangle)
            origin = sensor_origin_for_window(
                rectangle,
                parked,
                self.IDLE.handle_start,
                pose,
            )
            return (
                origin[0] + self.IDLE.handle_start[0],
                origin[1] + self.IDLE.handle_start[1],
            )
        return (
            pose.offset_x / SensorPoseTracker.MAX_OFFSET_X_PX * 1000.0,
            -pose.offset_y / SensorPoseTracker.MAX_OFFSET_Y_PX * 1000.0,
        )

    def _track_sensor_motion_axis(self, pose: SensorPose, now: float) -> None:
        point = self._sensor_motion_screen_point(pose)
        previous = getattr(self, "_sensor_motion_point", None)
        last_motion_at = getattr(self, "_last_sensor_motion_at", 0.0)
        if (
            previous is None
            or last_motion_at <= 0.0
            or now - last_motion_at > self.SENSOR_MOTION_GESTURE_GAP_SECONDS
        ):
            self._sensor_motion_xx = 0.0
            self._sensor_motion_xy = 0.0
            self._sensor_motion_yy = 0.0
        else:
            delta_x = point[0] - previous[0]
            delta_y = point[1] - previous[1]
            if math.hypot(delta_x, delta_y) >= 0.5:
                self._sensor_motion_xx += delta_x * delta_x
                self._sensor_motion_xy += delta_x * delta_y
                self._sensor_motion_yy += delta_y * delta_y
                angle = 0.5 * math.atan2(
                    2.0 * self._sensor_motion_xy,
                    self._sensor_motion_xx - self._sensor_motion_yy,
                )
                self._sensor_motion_direction = (
                    math.cos(angle),
                    math.sin(angle),
                )
        self._sensor_motion_point = point

    def set_sensor_pose(self, pose: SensorPose) -> None:
        """Apply the latest relative IMU pose to the parked on-screen whip."""
        now = time.perf_counter()
        self._sensor_pose_updated_at = now
        if pose.moving:
            self._track_sensor_motion_axis(pose, now)
            self._last_sensor_motion_at = now
            self._sensor_pose_target = pose
        else:
            # A stationary *orientation* still has a position. Do not freeze
            # halfway through interpolation or discard the last low-speed turn.
            self._sensor_pose_target = pose
        if (
            self._manual_armed
            or self._animation_after is not None
            or getattr(self, "_scare_active", False)
            or getattr(self, "_settings_open", False)
        ):
            return
        self._advance_sensor_pose(0.48)
        if self._target_available():
            self._sync_position()
            self._show_idle_hitbox()

    def _advance_sensor_pose(self, amount: float = 0.30) -> bool:
        target = self._sensor_pose_target
        now = time.perf_counter()
        stream_quiet = now - self._sensor_pose_updated_at
        if stream_quiet >= self.SENSOR_RETURN_DELAY_SECONDS:
            target = SensorPose(0.0, 0.0, 0.0, 0.0)
            self._sensor_pose_target = target
        current = self._sensor_pose_current
        updated = SensorPose(
            current.offset_x + (target.offset_x - current.offset_x) * amount,
            current.offset_y + (target.offset_y - current.offset_y) * amount,
            current.angle_degrees
            + (target.angle_degrees - current.angle_degrees) * amount,
            current.activity + (target.activity - current.activity) * amount,
            target.moving,
        )
        changed = (
            abs(updated.offset_x - current.offset_x) > 0.05
            or abs(updated.offset_y - current.offset_y) > 0.05
            or abs(updated.angle_degrees - current.angle_degrees) > 0.08
        )
        self._sensor_pose_current = updated
        remaining = getattr(self, "_sensor_return_remaining", 0.0) * (1.0-amount)
        self._sensor_return_remaining = remaining if remaining > .001 else 0.0
        return changed

    def attach(self, hwnd: int) -> None:
        if self._target_hwnd is not None and self._target_hwnd != int(hwnd):
            self._hide_scare(restore=False)
            self.disarm_manual(log=False)
            self._damage_items.clear()
            self._damage_photo_refs.clear()
            self._damage_render_key = None
            self._last_damage_strike_at = 0.0
            self._pcb_backplane = None
            self._pcb_backplane_size = (0, 0)
            self._sensor_physics = None
        self._target_hwnd = int(hwnd)
        if not getattr(self, "_settings_open", False) and self._target_available() and self._sync_position():
            self.window.deiconify()
            self._make_click_through()
            self._show_idle_hitbox()
        else:
            self.window.withdraw()
            self.hit_window.withdraw()
            self.capture_window.withdraw()
            self.damage_window.withdraw()
            self.scare_window.withdraw()

    def detach(self) -> None:
        self.disarm_manual(log=False)
        self._cancel_animation()
        self._hide_scare(restore=False)
        self._target_hwnd = None
        self._sensor_physics = None
        self.hit_window.withdraw()
        self.capture_window.withdraw()
        self.window.withdraw()
        self.damage_window.withdraw()

    def play(self) -> bool:
        presentation = getattr(self, '_presentation', None)
        if presentation is not None:
            self._begin_presentation_strike()
        if getattr(self, "_settings_open", False):
            return False
        if getattr(self, "_scare_active", False):
            return False
        if not self._target_available():
            self._log("视觉反馈跳过：当前没有可见的 Codex 窗口")
            return False
        if self._manual_armed:
            cursor = _cursor_position()
            if cursor is not None:
                return self.play_at(cursor)
        self._cancel_animation()
        self._sync_position()
        self._set_sensor_animation_poses()
        rectangle = _window_rectangle(self._target_hwnd)
        if rectangle is not None:
            self._animation_strike_pose = fit_pose_tip_to_window(
                self._animation_strike_pose,
                self._visual_origin,
                rectangle,
            )
        self._pending_impact_screen = (
            self._visual_origin[0] + self._animation_strike_pose.cord[-1][0],
            self._visual_origin[1] + self._animation_strike_pose.cord[-1][1],
        )
        self._pending_damage_direction = self._sensor_motion_direction
        self.hit_window.withdraw()
        if not self._animations_enabled:
            self._draw_pose(self._animation_strike_pose)
            self._current_pose = self._animation_strike_pose
            self._show_impact(self._animation_strike_pose.cord[-1])
            self._play_sound(self._crack_sound)
            self._maybe_record_damage(
                self._pending_impact_screen,
                self._pending_damage_direction,
            )
            self._start_shake()
            self._animation_after = self._root.after(
                160, self._finish_reduced_motion
            )
            return True
        self._animation_started_at = time.perf_counter()
        self._impact_fired = False
        self._animate_whip()
        return True

    def play_at(self, screen_point: Point) -> bool:
        """Play a strike whose impact tip lands on a screen coordinate."""
        self._begin_presentation_strike()
        if getattr(self, "_scare_active", False):
            return False
        if not self._target_available():
            self._log("鼠标抽打跳过：当前没有可见的 Codex 窗口")
            return False
        self._cancel_animation()
        self._set_fixed_animation_poses()
        self._pending_impact_screen = screen_point
        self._pending_damage_direction = getattr(
            self,
            "_manual_motion_direction",
            (1.0, 0.0),
        )
        origin = (
            screen_point[0] - self.STRIKE.cord[-1][0],
            screen_point[1] - self.STRIKE.cord[-1][1],
        )
        self._move_visual(origin)
        self.hit_window.withdraw()
        if not self.window.winfo_viewable():
            self.window.deiconify()
            self._make_click_through()
        self._raise_visual()
        if not self._animations_enabled:
            self._draw_pose(self.STRIKE)
            self._show_impact(self.STRIKE.cord[-1])
            self._play_sound(self._crack_sound)
            self._maybe_record_damage(
                screen_point,
                self._pending_damage_direction,
            )
            self._start_shake()
            self._animation_after = self._root.after(
                160, self._finish_reduced_motion
            )
            return True
        self._animation_started_at = time.perf_counter()
        self._impact_fired = False
        self._animate_whip()
        return True

    def _target_available(self) -> bool:
        if self._target_hwnd is None:
            return False
        if sys.platform == "darwin":
            return _target_exists(self._target_hwnd)
        if os.name != "nt":
            return False
        return bool(
            _user32.IsWindow(self._target_hwnd)
            and _user32.IsWindowVisible(self._target_hwnd)
            and not _user32.IsIconic(self._target_hwnd)
        )

    def _move_visual(self, origin: Point) -> None:
        if getattr(self, "_settings_open", False):
            return
        x = round(origin[0])
        y = round(origin[1])
        self._visual_origin = (float(x), float(y))
        if os.name != "nt":
            self.window.geometry(f"{self.WIDTH}x{self.HEIGHT}{x:+d}{y:+d}")
            return
        _user32.SetWindowPos(
            self._overlay_hwnd,
            HWND_TOPMOST,
            x,
            y,
            self.WIDTH,
            self.HEIGHT,
            SWP_NOACTIVATE | SWP_SHOWWINDOW,
        )

    def _default_origin(self, rectangle: WindowRectangle) -> Point:
        origin = (
            (rectangle.left + rectangle.right) / 2.0 - self.IDLE.handle_start[0],
            (rectangle.top + rectangle.bottom) / 2.0 - self.IDLE.handle_start[1],
        )
        return self._clamp_origin(rectangle, origin)

    def auto_center_sensor(self) -> None:
        """Return the sensor-controlled grip without resetting the cord solver."""
        if self._manual_armed or getattr(self, "_drag_start", None) is not None:
            return  # Never seize a whip currently controlled with the mouse.
        # Follow the decay of the artificial return separately from measured
        # handle motion; the display return must not launch the cord.
        self._sensor_return_remaining = 1.0
        self.reset_parking_to_center(preserve_physics=True, persist=False)

    def reset_parking_to_center(self, *, preserve_physics: bool = False, persist: bool = True) -> None:
        """Use the Codex-window center as the calibrated neutral handle point."""
        self._parking_position = WhipParkingPosition(0.5, 0.5)
        if persist:
            try:
                save_parking_position(self._position_path, self._parking_position)
            except OSError as exc:
                self._log(f"鞭子中心位置保存失败：{exc}")
        self._sensor_pose_target = SensorPose(0.0, 0.0, 0.0, 0.0, False)
        if not preserve_physics:
            self._sensor_return_remaining = 0.0
            self._sensor_pose_current = SensorPose(0.0, 0.0, 0.0, 0.0, False)
            self._sensor_physics = None
        self._sensor_motion_point = None
        self._sync_position()

    def _clamp_origin(self, rectangle: WindowRectangle, origin: Point) -> Point:
        padding = 18.0
        handle_x, handle_y = self.IDLE.handle_start
        minimum_x = rectangle.left + padding - handle_x
        maximum_x = rectangle.right - padding - handle_x
        minimum_y = rectangle.top + padding - handle_y
        maximum_y = rectangle.bottom - padding - handle_y
        if maximum_x < minimum_x:
            minimum_x = maximum_x = (
                rectangle.left + rectangle.right
            ) / 2.0 - handle_x
        if maximum_y < minimum_y:
            minimum_y = maximum_y = (
                rectangle.top + rectangle.bottom
            ) / 2.0 - handle_y
        return (
            float(min(maximum_x, max(minimum_x, round(origin[0])))),
            float(min(maximum_y, max(minimum_y, round(origin[1])))),
        )

    def _parked_origin(self, rectangle: WindowRectangle) -> Point:
        if self._parking_position is None:
            return self._default_origin(rectangle)
        origin = origin_from_parking_position(
            rectangle, self._parking_position, self.IDLE.handle_start
        )
        return self._clamp_origin(rectangle, origin)

    def _show_idle_hitbox(self) -> None:
        if self._manual_armed or not self._target_available():
            self.hit_window.withdraw()
            return
        padding = 20
        presentation = getattr(self, '_presentation', None)
        hit_pose = presentation.hit_pose if presentation is not None else self.IDLE
        x1 = round(
            self._visual_origin[0]
            + min(hit_pose.handle_start[0], hit_pose.handle_end[0])
            - padding
        )
        y1 = round(
            self._visual_origin[1]
            + min(hit_pose.handle_start[1], hit_pose.handle_end[1])
            - padding
        )
        x2 = round(
            self._visual_origin[0]
            + max(hit_pose.handle_start[0], hit_pose.handle_end[0])
            + padding
        )
        y2 = round(
            self._visual_origin[1]
            + max(hit_pose.handle_start[1], hit_pose.handle_end[1])
            + padding
        )
        self._set_geometry(self.hit_window, WindowRectangle(x1, y1, x2, y2))
        if not self.hit_window.winfo_viewable():
            self.hit_window.deiconify()
        self._make_input_window(self.hit_window)
        self._raise_visual()

    def _sync_capture_window(self) -> None:
        # Manual-whip mode stays active outside Codex. Cover the complete
        # virtual desktop so left/right clicks remain captured on every monitor.
        self._set_geometry(self.capture_window, _virtual_screen_rectangle())
        if not self.capture_window.winfo_viewable():
            self.capture_window.deiconify()
        self._make_input_window(self.capture_window)
        self._raise_visual()

    def _position_cursor_whip(self, point: Point) -> None:
        origin = (
            point[0] - self.CURSOR.handle_start[0],
            point[1] - self.CURSOR.handle_start[1],
        )
        self._move_visual(origin)
        self._draw_pose(self.CURSOR)
        self._current_pose = self.CURSOR

    def _remember_manual_motion(self, point: Point) -> None:
        previous = getattr(self, "_manual_target", None)
        if previous is not None:
            direction = (point[0] - previous[0], point[1] - previous[1])
            if math.hypot(*direction) >= 1.0:
                self._manual_motion_direction = direction
        elif not hasattr(self, "_manual_motion_direction"):
            self._manual_motion_direction = (1.0, 0.0)
        self._manual_target = point

    def _handle_press(self, event: tk.Event) -> str:
        if self._manual_armed or not self._target_available():
            return "break"
        self._drag_start = (float(event.x_root), float(event.y_root))
        self._drag_origin = self._visual_origin
        self._dragging = False
        try:
            self.hit_window.grab_set()
        except tk.TclError:
            pass
        return "break"

    def _handle_drag(self, event: tk.Event) -> str:
        if self._drag_start is None or self._drag_origin is None:
            return "break"
        current = (float(event.x_root), float(event.y_root))
        if not self._dragging and not movement_exceeds_drag_threshold(
            self._drag_start, current, self.DRAG_THRESHOLD
        ):
            return "break"
        self._dragging = True
        rectangle = (
            _window_rectangle(self._target_hwnd)
            if self._target_hwnd is not None
            else None
        )
        if rectangle is None:
            return "break"
        origin = self._clamp_origin(
            rectangle,
            (
                self._drag_origin[0] + current[0] - self._drag_start[0],
                self._drag_origin[1] + current[1] - self._drag_start[1],
            ),
        )
        self._move_visual(origin)
        self._show_idle_hitbox()
        return "break"

    def _handle_release(self, _event: tk.Event) -> str:
        try:
            self.hit_window.grab_release()
        except tk.TclError:
            pass
        was_dragging = self._dragging
        self._drag_start = None
        self._drag_origin = None
        self._dragging = False
        if was_dragging:
            rectangle = (
                _window_rectangle(self._target_hwnd)
                if self._target_hwnd is not None
                else None
            )
            if rectangle is not None:
                self._parking_position = parking_position_from_origin(
                    rectangle, self._visual_origin, self.IDLE.handle_start
                )
                try:
                    save_parking_position(
                        self._position_path, self._parking_position
                    )
                    self._log("鞭子位置已保存")
                except OSError as exc:
                    self._log(f"鞭子位置保存失败：{exc}")
            self._show_idle_hitbox()
        else:
            self.arm_manual()
        return "break"

    def arm_manual(self) -> bool:
        if not self._target_available():
            return False
        cursor = _cursor_position()
        if cursor is None:
            return False
        presentation = getattr(self, '_presentation', None)
        if presentation is not None:
            presentation.hero._set_clock(False)
        self._cancel_animation()
        self._manual_armed = True
        self._manual_motion_direction = (1.0, 0.0)
        self.hit_window.withdraw()
        self._sync_capture_window()
        self._manual_target = cursor
        if self._animations_enabled:
            self._physics = CartoonWhipPhysics(cursor, scale=0.58)
            self._physics_last_time = time.perf_counter()
            self._physics_frame()
        else:
            self._position_cursor_whip(cursor)
        self._log("物理鞭子已拿起：移动鼠标挥动手柄，左键抽打，右键或 Esc 放回")
        return True

    def disarm_manual(self, *, log: bool = True) -> None:
        was_armed = self._manual_armed
        self._manual_armed = False
        self._cancel_physics()
        self._cancel_animation()
        self.capture_window.withdraw()
        self._hide_impact()
        self._draw_pose(self.IDLE)
        self._current_pose = self.IDLE
        if self._target_available():
            self._sync_position()
            self._show_idle_hitbox()
        else:
            self.hit_window.withdraw()
        if was_armed and log:
            self._log("鼠标鞭子已解除并回到停放位置")

    def _handle_manual_motion(self, event: tk.Event) -> str:
        if self._manual_armed:
            point = _cursor_position() or (
                round(float(event.x_root)), round(float(event.y_root))
            )
            self._remember_manual_motion(point)
            if not self._animations_enabled:
                self._position_cursor_whip(point)
        return "break"

    def _handle_manual_strike(self, event: tk.Event) -> str:
        if not self._manual_armed:
            return "break"
        now = time.monotonic()
        if (now - self._manual_last_strike_at) * 1000.0 < self.MANUAL_DEBOUNCE_MS:
            return "break"
        self._manual_last_strike_at = now
        point = _cursor_position() or (
            round(float(event.x_root)), round(float(event.y_root))
        )
        self._remember_manual_motion(point)
        self._pending_damage_direction = self._manual_motion_direction
        if getattr(self, "_animations_enabled", False) and getattr(self, "_physics", None) is not None:
            self._manual_strike_started_at = time.perf_counter()
            self._manual_strike_impact = point
            self._manual_strike_fired = False
            self._manual_stroke = ManualWhipStroke(self._physics, point)
        else:
            self.play_at(point)
        if self._manual_whip is not None:
            try:
                self._manual_whip(point)
            except Exception as exc:
                self._log(f"鼠标抽打事件处理失败：{exc}")
        return "break"

    def _handle_manual_cancel(self, _event: tk.Event) -> str:
        self.disarm_manual()
        return "break"

    def _handle_clock_toggle(self, _event: tk.Event) -> str:
        presentation = getattr(self, '_presentation', None)
        if presentation is not None and not self._manual_armed and self._target_available():
            presentation.toggle_clock()
        return "break"

    def set_presentation(self, **state) -> None:
        if getattr(self, '_presentation_failed', False):
            return
        try:
            if getattr(self, '_presentation', None) is None:
                from .overlay_presentation import OverlayPresentation
                self._presentation = OverlayPresentation(self)
            self._presentation.update(**state)
        except Exception as exc:
            self._disable_presentation(exc)

    def _begin_presentation_strike(self):
        presentation = getattr(self, '_presentation', None)
        if presentation is not None:
            try:
                presentation.begin_strike()
            except Exception as exc:
                self._disable_presentation(exc)

    def _disable_presentation(self, exc):
        presentation = getattr(self, '_presentation', None)
        self._presentation = None
        self._presentation_failed = True
        if presentation is not None:
            try:
                presentation.close()
            except Exception:
                pass
        self._whip_drawing.draw(self._preview_pose)
        self._log(f'附加状态动画已停用，保留正常抽打：{type(exc).__name__}')

    def _physics_frame(self) -> None:
        self._physics_after = None
        if not self._manual_armed or self._physics is None:
            return
        now = time.perf_counter()
        dt_seconds = min(0.10, max(0.0, now - self._physics_last_time))
        self._physics_last_time = now
        cursor = _cursor_position() or self._manual_target or self._physics.position
        self._manual_target = cursor
        impact = self._manual_strike_impact
        started = self._manual_strike_started_at
        stroke = self._manual_stroke
        if started is not None and impact is not None and stroke is not None:
            elapsed = now - started
            pose = stroke.sample(elapsed, cursor)
            if elapsed >= stroke.IMPACT_SECONDS and not self._manual_strike_fired:
                # Render the contact pose even if a busy GUI skips its exact ms.
                pose = stroke.strike
                self._manual_strike_fired = True
                self._play_sound(self._crack_sound)
                self._maybe_record_damage(impact, self._pending_damage_direction)
                self._start_shake()
            if elapsed >= stroke.DURATION_SECONDS and pose is not stroke.strike:
                self._manual_strike_started_at = None
                self._manual_strike_impact = None
                self._manual_strike_fired = False
                self._manual_stroke = None
                self._hide_impact()
            self._physics.adopt_pose(pose)
        else:
            pose = self._physics.step(cursor, dt_seconds)
        origin = (
            pose.handle_start[0] - self.CURSOR.handle_start[0],
            pose.handle_start[1] - self.CURSOR.handle_start[1],
        )
        local_pose = self._pose_to_local(pose, origin)
        self._move_visual(origin)
        self._draw_pose(local_pose)
        self._current_pose = local_pose
        if self._manual_strike_fired and impact is not None:
            self._show_impact((impact[0] - origin[0], impact[1] - origin[1]))
        self._raise_visual()
        self._physics_after = self._root.after(self.FRAME_MS, self._physics_frame)

    @staticmethod
    def _pose_to_local(pose: WhipPose, origin: Point) -> WhipPose:
        def local(point: Point) -> Point:
            return point[0] - origin[0], point[1] - origin[1]

        return WhipPose(
            local(pose.handle_start),
            local(pose.handle_end),
            tuple(local(point) for point in pose.cord),
        )

    def _cancel_physics(self) -> None:
        if self._physics_after is not None:
            try:
                self._root.after_cancel(self._physics_after)
            except tk.TclError:
                pass
            self._physics_after = None
        self._physics = None
        self._manual_target = None
        self._manual_strike_started_at = None
        self._manual_strike_impact = None
        self._manual_strike_fired = False
        self._manual_stroke = None

    def _schedule_sync(self) -> None:
        # Include rendering time in the frame budget instead of adding it to
        # every frame. Do not catch up with a burst after a blocked UI thread.
        spent = (time.perf_counter() - getattr(self, "_sync_started_at", time.perf_counter())) * 1000
        delay = max(1, math.ceil(self.SYNC_INTERVAL_MS - spent))
        self._sync_after = self._root.after(delay, self._sync_tick)

    def set_settings_open(self, opened: bool) -> None:
        self._settings_open = opened
        if opened:
            self.disarm_manual(log=False)
            self._cancel_animation()
            for window in (self.window, self.hit_window, self.capture_window, self.damage_window):
                window.withdraw()

    def _sync_tick(self) -> None:
        now = time.perf_counter()
        elapsed = min(.10, max(0.0, now - getattr(self, "_sync_started_at", now - .016)))
        self._sync_started_at = now
        self._sync_after = None
        if getattr(self, "_settings_open", False):
            self._schedule_sync()
            return
        if self._target_hwnd is not None:
            if self._target_available():
                if self._scare_active:
                    escape_down = _escape_pressed()
                    if escape_down:
                        self._hide_scare()
                    self._schedule_sync()
                    return
                if self._manual_armed:
                    cursor = _cursor_position()
                    escape_down = _escape_pressed()
                    if escape_down:
                        self.disarm_manual()
                    else:
                        self._sync_capture_window()
                        rectangle = _window_rectangle(self._target_hwnd)
                        if rectangle is not None:
                            self._sync_damage_overlay(rectangle)
                elif self._animation_after is None and not self._dragging:
                    # Preserve the old 30% / 50 ms follow response at any FPS.
                    self._advance_sensor_pose(1.0 - .70 ** (elapsed / .050))
                    self._sync_position()
                    self._show_idle_hitbox()
                if not self.window.winfo_viewable():
                    self.window.deiconify()
                    self._make_click_through()
                presentation = getattr(self, '_presentation', None)
                if presentation is not None:
                    try:
                        if presentation.owns_geometry:
                            self._whip_drawing.hide()
                        else:
                            self._whip_drawing.draw(self._preview_pose)
                        presentation.render(_cursor_position())
                    except Exception as exc:
                        self._disable_presentation(exc)
            else:
                self.disarm_manual(log=False)
                self.hit_window.withdraw()
                self.capture_window.withdraw()
                self.window.withdraw()
                self.damage_window.withdraw()
                self._hide_scare(restore=False)
        self._schedule_sync()

    def _sync_position(self) -> bool:
        if getattr(self, "_settings_open", False):
            return False
        if self._target_hwnd is None:
            return False
        rectangle = _window_rectangle(self._target_hwnd)
        if rectangle is None:
            return False
        self._sync_damage_overlay(rectangle)
        if self._manual_armed:
            self._sync_capture_window()
            return True
        parked = self._parked_origin(rectangle)
        sensor = self._sensor_pose_current
        origin = sensor_origin_for_window(
            rectangle,
            parked,
            self.IDLE.handle_start,
            sensor,
        )
        self._move_visual(origin)
        if not self._animations_enabled:
            self._draw_pose(self.IDLE)
            self._current_pose = self.IDLE
            return True
        target_handle = (
            origin[0] + self.IDLE.handle_start[0],
            origin[1] + self.IDLE.handle_start[1],
        )
        now = time.perf_counter()
        if (
            self._sensor_physics is None
            or math.dist(self._sensor_physics.position, target_handle)
            > max(380.0, math.hypot(rectangle.width, rectangle.height) * 1.15)
        ):
            self._sensor_physics = CartoonWhipPhysics(target_handle)
            self._sensor_physics_last_time = now
        dt_seconds = min(
            0.10,
            max(0.0, now - self._sensor_physics_last_time),
        )
        self._sensor_physics_last_time = now
        if getattr(self, "_sensor_return_remaining", 0.0) > 0:
            self._sensor_physics.move_without_impulse(target_handle, sensor.angle_degrees)
        pose = self._sensor_physics.step(
            target_handle,
            dt_seconds,
            aim_offset_degrees=sensor.angle_degrees,
            kinematic=True,
        )
        local_pose = self._pose_to_local(pose, origin)
        self._draw_pose(local_pose)
        self._current_pose = local_pose
        return True

    def _animate_whip(self) -> None:
        elapsed_ms = (time.perf_counter() - self._animation_started_at) * 1000.0
        # A busy UI can skip the whole impact interval. Never lose the hit.
        if elapsed_ms >= self.IMPACT_AT_MS and not self._impact_fired:
            self._impact_fired = True
            self._show_impact(self._animation_strike_pose.cord[-1])
            self._play_sound(self._crack_sound)
            if self._pending_impact_screen is not None:
                self._maybe_record_damage(self._pending_impact_screen, self._pending_damage_direction)
            self._start_shake()
        if elapsed_ms < 65.0:
            progress = cubic_bezier_ease_in_out(elapsed_ms / 65.0)
            pose = interpolate_pose(
                self._animation_start_pose,
                self._animation_windup_pose,
                progress,
            )
        elif elapsed_ms < self.IMPACT_AT_MS:
            progress = cubic_bezier_ease_in_out((elapsed_ms - 65.0) / 90.0)
            pose = interpolate_pose(
                self._animation_windup_pose,
                self._animation_strike_pose,
                progress,
            )
        elif elapsed_ms < 205.0:
            progress = cubic_bezier_ease_in_out((elapsed_ms - 155.0) / 50.0)
            pose = interpolate_pose(
                self._animation_strike_pose,
                self._animation_recoil_pose,
                progress,
            )
        elif elapsed_ms < 245.0:
            progress = cubic_bezier_ease_in_out((elapsed_ms - 205.0) / 40.0)
            pose = interpolate_pose(
                self._animation_recoil_pose,
                self._animation_settle_pose,
                progress,
            )
        elif elapsed_ms < self.WHIP_DURATION_MS:
            progress = cubic_bezier_ease_in_out((elapsed_ms - 245.0) / 35.0)
            pose = interpolate_pose(
                self._animation_settle_pose,
                self._animation_idle_pose,
                progress,
            )
        else:
            self._draw_pose(self._animation_idle_pose)
            self._current_pose = self._animation_idle_pose
            self._animation_after = None
            self._hide_impact()
            self._sync_position()
            self._show_idle_hitbox()
            return

        self._draw_pose(pose)
        self._current_pose = pose
        self._animation_after = self._root.after(self.FRAME_MS, self._animate_whip)

    def _set_fixed_animation_poses(self) -> None:
        self._animation_start_pose = self.IDLE
        self._animation_windup_pose = self.WINDUP
        self._animation_strike_pose = self.STRIKE
        self._animation_recoil_pose = self.RECOIL
        self._animation_settle_pose = self.SETTLE
        self._animation_idle_pose = self.IDLE

    def _set_sensor_animation_poses(self) -> None:
        current = self._current_pose
        anchor = current.handle_start
        angle_degrees = self._sensor_pose_current.angle_degrees
        cord_point_count = len(current.cord)

        def phase(pose: WhipPose) -> WhipPose:
            return transform_whip_pose(
                _resample_whip_pose(pose, cord_point_count),
                anchor,
                angle_degrees,
            )

        self._animation_start_pose = current
        self._animation_windup_pose = phase(self.WINDUP)
        self._animation_strike_pose = phase(self.STRIKE)
        self._animation_recoil_pose = phase(self.RECOIL)
        self._animation_settle_pose = phase(self.SETTLE)
        self._animation_idle_pose = phase(self.IDLE)

    def _draw_pose(self, pose: WhipPose) -> None:
        self._preview_pose = pose
        presentation = getattr(self, '_presentation', None)
        if presentation is None or not presentation.owns_geometry:
            self._whip_drawing.draw(pose)

    def preview_frame(self) -> tuple[WhipPose, Point]:
        """Read-only view of the frame already drawn; never advances physics."""
        pose = self._preview_pose
        rectangle = _window_rectangle(self._target_hwnd) if self._target_hwnd is not None else None
        if rectangle is None:
            return pose, (0.0, 0.0)
        anchor = (self._visual_origin[0] + pose.handle_start[0],
                  self._visual_origin[1] + pose.handle_start[1])
        return pose, ((anchor[0] - rectangle.left) / max(1, rectangle.width) - 0.5,
                      (anchor[1] - rectangle.top) / max(1, rectangle.height) - 0.5)


    def _show_impact(self, point: Point) -> None:
        x, y = point
        segments = (
            (x - 34, y, x - 13, y),
            (x + 13, y, x + 34, y),
            (x, y - 34, x, y - 13),
            (x, y + 13, x, y + 34),
            (x - 25, y - 25, x - 10, y - 10),
            (x + 10, y - 10, x + 25, y - 25),
            (x - 25, y + 25, x - 10, y + 10),
            (x + 10, y + 10, x + 25, y + 25),
        )
        for item, coordinates in zip(self._impact_items, segments):
            self.canvas.coords(item, *coordinates)
            self.canvas.itemconfigure(item, state="normal")
        self.canvas.coords(self._impact_ring, x - 18, y - 18, x + 18, y + 18)
        self.canvas.itemconfigure(self._impact_ring, state="normal")

    def _hide_impact(self) -> None:
        for item in self._impact_items:
            self.canvas.itemconfigure(item, state="hidden")
        self.canvas.itemconfigure(self._impact_ring, state="hidden")

    def _finish_reduced_motion(self) -> None:
        self._animation_after = None
        self._hide_impact()
        self._draw_pose(self.IDLE)
        self._current_pose = self.IDLE
        self._pending_impact_screen = None
        self._sync_position()
        self._show_idle_hitbox()

    def _start_shake(self) -> None:
        if self._target_hwnd is None or not _target_exists(self._target_hwnd):
            return
        if _target_is_fullscreen(self._target_hwnd):
            self._log("Codex 当前已最大化或全屏；为避免改变窗口状态，本次只播放挥鞭动画")
            return
        rectangle = _window_rectangle(self._target_hwnd)
        if rectangle is None:
            return
        self._restore_shake()
        self._shake_hwnd = self._target_hwnd
        self._shake_base = rectangle
        self._shake_started_at = time.perf_counter()
        self._shake_frame()

    def _shake_frame(self) -> None:
        if self._shake_hwnd is None or self._shake_base is None:
            return
        elapsed_ms = (time.perf_counter() - self._shake_started_at) * 1000.0
        progress = elapsed_ms / self.SHAKE_DURATION_MS
        if progress >= 1.0 or not _target_exists(self._shake_hwnd):
            self._restore_shake()
            return
        offset_x, offset_y = shake_offset(progress)
        _move_target_window(
            self._shake_hwnd,
            self._shake_base.left + offset_x,
            self._shake_base.top + offset_y,
        )
        self._sync_damage_overlay(
            WindowRectangle(
                self._shake_base.left + offset_x,
                self._shake_base.top + offset_y,
                self._shake_base.right + offset_x,
                self._shake_base.bottom + offset_y,
            )
        )
        self._shake_after = self._root.after(self.FRAME_MS, self._shake_frame)

    def _restore_shake(self) -> None:
        if self._shake_after is not None:
            try:
                self._root.after_cancel(self._shake_after)
            except tk.TclError:
                pass
            self._shake_after = None
        if (
            self._shake_hwnd is not None
            and self._shake_base is not None
            and _target_exists(self._shake_hwnd)
        ):
            _move_target_window(
                self._shake_hwnd,
                self._shake_base.left,
                self._shake_base.top,
            )
            self._sync_damage_overlay(self._shake_base)
        self._shake_hwnd = None
        self._shake_base = None

    def _cancel_animation(self) -> None:
        if self._animation_after is not None:
            try:
                self._root.after_cancel(self._animation_after)
            except tk.TclError:
                pass
            self._animation_after = None
        self._restore_shake()
        self._hide_impact()
        self._pending_impact_screen = None
        self._draw_pose(self.IDLE)
        self._current_pose = self.IDLE

    def close(self) -> None:
        presentation = getattr(self, '_presentation', None)
        if presentation is not None:
            presentation.close()
            self._presentation = None
        self.disarm_manual(log=False)
        self._cancel_animation()
        self._hide_scare(restore=False)
        if self._sync_after is not None:
            try:
                self._root.after_cancel(self._sync_after)
            except tk.TclError:
                pass
            self._sync_after = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass
        for input_window in (
            self.hit_window,
            self.capture_window,
            self.damage_window,
            self.scare_window,
        ):
            try:
                input_window.destroy()
            except tk.TclError:
                pass
