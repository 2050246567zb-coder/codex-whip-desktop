import io
import math
import os
from types import SimpleNamespace
import wave

import pytest
from PIL import Image

from codex_whip.effects import (
    CartoonWhipPhysics,
    ManualWhipStroke,
    CodexWhipEffects,
    WhipParkingPosition,
    WhipPose,
    WindowRectangle,
    build_damage_layer,
    build_damage_patch,
    build_directional_tear_mask,
    build_tear_mask,
    build_whip_crack_wav,
    bundled_asset_path,
    catmull_rom_points,
    cover_image,
    cubic_bezier_ease_in_out,
    extend_whip_pose,
    fit_pose_tip_to_window,
    group_damage_masks,
    harden_alpha,
    healing_visibility,
    interpolate_pose,
    load_whip_strike_wav,
    load_parking_position,
    movement_exceeds_drag_threshold,
    merge_damage_masks,
    origin_from_parking_position,
    parking_position_from_origin,
    save_parking_position,
    scare_animation_total_ms,
    scare_eye_openness,
    sensor_origin_for_window,
    shake_offset,
    transform_whip_pose,
)


from codex_whip.gui import CodexWhipWindow
from codex_whip.sensor_pose import SensorPose


@pytest.mark.skipif(os.name != 'nt', reason='Windows foreground policy')
def test_overlay_only_available_while_codex_is_foreground(monkeypatch):
    from codex_whip import effects as module
    effect = object.__new__(CodexWhipEffects)
    effect._target_hwnd = 101
    fake = SimpleNamespace(IsWindow=lambda _: True, IsWindowVisible=lambda _: True,
                           IsIconic=lambda _: False, GetForegroundWindow=lambda: 202,
                           GetAncestor=lambda hwnd, _: hwnd)
    monkeypatch.setattr(module, '_user32', fake)
    assert not effect._target_available()
    effect._product_window_roots = frozenset({202, 303})
    assert effect._target_available()
    assert effect._overlay_z_anchor() == 202
    fake.GetForegroundWindow = lambda: 404
    assert not effect._target_available()
    fake.GetForegroundWindow = lambda: 101
    assert effect._target_available()
    assert effect._overlay_z_anchor() == module.HWND_TOPMOST


def test_overlay_activity_does_not_require_settings_to_have_been_opened():
    effect = object.__new__(CodexWhipEffects)
    effect._target_available = lambda: True
    assert effect.target_active()
    effect._settings_open = True
    assert not effect.target_active()


class FakeEffects:
    def __init__(self) -> None:
        self.attached: list[int] = []
        self.detached = 0

    def attach(self, handle: int) -> None:
        self.attached.append(handle)

    def detach(self) -> None:
        self.detached += 1


def test_delayed_animation_frame_still_fires_hit_exactly_once(monkeypatch):
    from unittest.mock import Mock
    effect=object.__new__(CodexWhipEffects)
    effect._animation_started_at=1.
    effect._impact_fired=False
    effect._animation_strike_pose=CodexWhipEffects.STRIKE
    effect._animation_idle_pose=CodexWhipEffects.IDLE
    effect._pending_impact_screen=(400,300)
    effect._pending_damage_direction=(1,0)
    effect._crack_sound=b'test'
    for name in ('_show_impact','_play_sound','_maybe_record_damage','_start_shake',
                 '_draw_pose','_hide_impact','_sync_position','_show_idle_hitbox'):
        setattr(effect,name,Mock())
    monkeypatch.setattr('codex_whip.effects.time.perf_counter',lambda:1.5)
    effect._animate_whip()
    effect._animate_whip()
    effect._play_sound.assert_called_once_with(b'test')
    effect._maybe_record_damage.assert_called_once_with((400,300),(1,0))
    effect._start_shake.assert_called_once()


def test_animation_curve_has_exact_endpoints_and_is_monotonic() -> None:
    values = [cubic_bezier_ease_in_out(index / 20) for index in range(21)]
    assert values[0] == pytest.approx(0.0, abs=0.00001)
    assert values[-1] == pytest.approx(1.0, abs=0.00001)
    assert values == sorted(values)


def test_red_eyes_flash_as_lines_twice_then_open_and_hold() -> None:
    assert scare_eye_openness(1999, 2000, 3000) == 0.0
    assert scare_eye_openness(2000, 2000, 3000) == pytest.approx(0.018)
    assert scare_eye_openness(2139, 2000, 3000) == pytest.approx(0.018)
    assert scare_eye_openness(2140, 2000, 3000) == 0.0
    assert scare_eye_openness(2250, 2000, 3000) == pytest.approx(0.018)
    assert scare_eye_openness(2390, 2000, 3000) == 0.0
    assert scare_eye_openness(2500, 2000, 3000) == pytest.approx(0.018)
    assert 0.018 < scare_eye_openness(2660, 2000, 3000) < 1.0
    assert scare_eye_openness(2820, 2000, 3000) == 1.0
    assert scare_eye_openness(5819, 2000, 3000) == 1.0
    assert scare_eye_openness(5820, 2000, 3000) == 0.0
    assert scare_animation_total_ms(2000, 3000) == 5820


def test_whip_pose_interpolation_preserves_geometry() -> None:
    start = WhipPose((0, 0), (10, 10), ((10, 10), (20, 20)))
    end = WhipPose((10, 20), (30, 40), ((30, 40), (50, 60)))

    halfway = interpolate_pose(start, end, 0.5)

    assert halfway.handle_start == (5.0, 10.0)
    assert halfway.handle_end == (20.0, 25.0)
    assert halfway.cord == ((20.0, 25.0), (35.0, 40.0))


def test_whip_pose_rejects_incompatible_control_points() -> None:
    start = WhipPose((0, 0), (1, 1), ((1, 1),))
    end = WhipPose((0, 0), (1, 1), ((1, 1), (2, 2)))

    with pytest.raises(ValueError):
        interpolate_pose(start, end, 0.5)


def test_pose_transform_moves_handle_and_preserves_every_segment_length() -> None:
    source = WhipPose(
        (10.0, 20.0),
        (25.0, 24.0),
        ((25.0, 24.0), (40.0, 35.0), (51.0, 58.0)),
    )

    transformed = transform_whip_pose(source, (500.0, 320.0), 37.0)

    assert transformed.handle_start == pytest.approx((500.0, 320.0))
    source_lengths = [
        math.dist(source.handle_start, source.handle_end),
        *(math.dist(first, second) for first, second in zip(source.cord, source.cord[1:])),
    ]
    transformed_lengths = [
        math.dist(transformed.handle_start, transformed.handle_end),
        *(
            math.dist(first, second)
            for first, second in zip(transformed.cord, transformed.cord[1:])
        ),
    ]
    assert transformed_lengths == pytest.approx(source_lengths)


def test_window_shake_starts_and_finishes_at_original_position() -> None:
    assert shake_offset(0.0) == (0, 0)
    assert shake_offset(1.0) == (0, 0)
    offsets = [shake_offset(index / 100) for index in range(1, 100)]
    assert max(abs(x) for x, _y in offsets) >= 18
    assert max(abs(y) for _x, y in offsets) >= 8


def test_exaggerated_strike_travels_across_the_overlay() -> None:
    idle_tip = CodexWhipEffects.IDLE.cord[-1]
    strike_tip = CodexWhipEffects.STRIKE.cord[-1]

    assert strike_tip[1] - idle_tip[1] >= 220
    assert len(CodexWhipEffects.IDLE.cord) == len(CodexWhipEffects.RECOIL.cord)
    all_points = [
        point
        for pose in (
            CodexWhipEffects.IDLE,
            CodexWhipEffects.WINDUP,
            CodexWhipEffects.STRIKE,
            CodexWhipEffects.RECOIL,
            CodexWhipEffects.SETTLE,
        )
        for point in (pose.handle_start, pose.handle_end, *pose.cord)
    ]
    assert all(0 <= x <= CodexWhipEffects.WIDTH for x, _y in all_points)
    assert all(0 <= y <= CodexWhipEffects.HEIGHT for _x, y in all_points)


def test_generated_whip_crack_is_a_short_pcm_wave() -> None:
    sound = build_whip_crack_wav()

    assert sound.startswith(b"RIFF")
    with wave.open(io.BytesIO(sound), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 44100
        assert audio.getnframes() == 9702


def test_layered_whip_recording_is_bundled() -> None:
    sound = load_whip_strike_wav()
    assert sound.startswith(b"RIFF")
    assert len(sound) > 50_000


def test_short_click_and_drag_are_distinguished_by_distance() -> None:
    assert not movement_exceeds_drag_threshold((100, 100), (104, 103))
    assert movement_exceeds_drag_threshold((100, 100), (106, 100))


def test_manual_strike_uses_live_cursor_instead_of_tk_event_coordinates(
    monkeypatch,
) -> None:
    from codex_whip import effects as effects_module

    effect = object.__new__(CodexWhipEffects)
    effect._manual_armed = True
    effect._manual_last_strike_at = 0.0
    played: list[tuple[int, int]] = []
    handled: list[tuple[int, int]] = []
    effect.play_at = played.append
    effect._manual_whip = handled.append
    effect._log = lambda _line: None
    monkeypatch.setattr(effects_module, "_cursor_position", lambda: (640, 360))

    result = effect._handle_manual_strike(SimpleNamespace(x_root=12, y_root=34))

    assert result == "break"
    assert played == [(640, 360)]
    assert handled == [(640, 360)]


def test_parking_position_round_trips_across_window_coordinates() -> None:
    rectangle = WindowRectangle(100, 50, 1100, 850)
    handle = CodexWhipEffects.IDLE.handle_start
    origin = (480.0, 140.0)

    position = parking_position_from_origin(rectangle, origin, handle)
    restored = origin_from_parking_position(rectangle, position, handle)

    assert restored == pytest.approx(origin)


def test_default_whip_handle_position_is_codex_window_center() -> None:
    effect = object.__new__(CodexWhipEffects)
    rectangle = WindowRectangle(100, 200, 1300, 1000)

    origin = effect._default_origin(rectangle)

    handle = effect.IDLE.handle_start
    assert origin[0] + handle[0] == 700.0
    assert origin[1] + handle[1] == 600.0


def test_sensor_calibration_persists_center_as_the_neutral_position(tmp_path) -> None:
    effect = object.__new__(CodexWhipEffects)
    effect._position_path = tmp_path / "overlay-position.json"
    effect._sensor_physics = object()
    effect._sensor_motion_point = (42.0, 21.0)
    synced: list[bool] = []
    effect._sync_position = lambda: synced.append(True) or True
    effect._log = lambda _line: None

    effect.reset_parking_to_center()

    assert effect._parking_position == WhipParkingPosition(0.5, 0.5)
    assert load_parking_position(effect._position_path) == WhipParkingPosition(0.5, 0.5)
    assert effect._sensor_pose_current == SensorPose(0.0, 0.0, 0.0, 0.0, False)
    assert effect._sensor_motion_point is None
    assert effect._sensor_physics is None
    assert synced == [True]


def test_parking_position_is_clamped_and_persisted(tmp_path) -> None:
    path = tmp_path / "overlay-position.json"

    save_parking_position(path, WhipParkingPosition(1.4, -0.3))

    assert load_parking_position(path) == WhipParkingPosition(1.0, 0.0)
    assert "schema_version" in path.read_text(encoding="utf-8")


def test_idle_auto_center_keeps_rope_and_interpolation_without_persisting(tmp_path):
    effect = object.__new__(CodexWhipEffects)
    effect._manual_armed = False
    effect._position_path = tmp_path / 'parking.json'
    effect._sensor_physics = rope = object()
    effect._sensor_pose_current = previous = SensorPose(80, 20, 3, .1)
    effect._sync_position = lambda: True
    effect.auto_center_sensor()
    assert effect._parking_position == WhipParkingPosition(.5, .5)
    assert effect._sensor_pose_target.offset_x == 0
    assert effect._sensor_pose_current is previous
    assert effect._sensor_physics is rope
    assert not effect._position_path.exists()


def test_idle_auto_center_does_not_take_over_mouse_whip():
    effect = object.__new__(CodexWhipEffects)
    effect._manual_armed = True
    effect._parking_position = before = WhipParkingPosition(.3, .4)
    effect.auto_center_sensor()
    assert effect._parking_position is before


def test_idle_auto_center_does_not_interrupt_mouse_drag():
    effect = object.__new__(CodexWhipEffects)
    effect._manual_armed = False
    effect._drag_start = (100, 100)
    effect._parking_position = before = WhipParkingPosition(.3, .4)
    effect.auto_center_sensor()
    assert effect._parking_position is before


def test_display_return_preserves_rope_velocities_and_does_not_launch_cord():
    import copy
    reference = CartoonWhipPhysics((500, 100))
    for i in range(25):
        reference.step((500+i*3, 100), kinematic=True)
    shifted = copy.deepcopy(reference)
    original = reference.pose()
    velocities = [(p.x-p.previous_x, p.y-p.previous_y) for p in shifted.points]
    delta = (-300, 240)
    anchor = (reference.position[0]+delta[0], reference.position[1]+delta[1])
    shifted.move_without_impulse(anchor, 0)
    for i, p in enumerate(shifted.points):
        assert (p.x-p.previous_x, p.y-p.previous_y) == pytest.approx(velocities[i])
        assert (p.x, p.y) == pytest.approx((original.cord[i][0]+delta[0], original.cord[i][1]+delta[1]))
    for _ in range(30):
        a = reference.step(reference.position, kinematic=True)
        b = shifted.step(anchor, kinematic=True)
        assert b.cord[-1] == pytest.approx((a.cord[-1][0]+delta[0], a.cord[-1][1]+delta[1]), abs=.001)
    assert shifted.pose().cord[-1] != (original.cord[-1][0]+delta[0], original.cord[-1][1]+delta[1])


def test_rotating_display_return_preserves_internal_speed():
    physics = CartoonWhipPhysics((500, 100))
    for i in range(10):
        physics.step((500+i*5, 100), kinematic=True)
    speeds = [math.hypot(p.x-p.previous_x, p.y-p.previous_y) for p in physics.points]
    distances = [math.dist((p.x,p.y), physics.position) for p in physics.points]
    physics.move_without_impulse((800, 300), 35)
    assert [math.hypot(p.x-p.previous_x, p.y-p.previous_y) for p in physics.points] == pytest.approx(speeds)
    assert [math.dist((p.x,p.y), physics.position) for p in physics.points] == pytest.approx(distances)


def test_display_return_transport_finishes_even_if_real_target_keeps_moving(monkeypatch):
    effect = object.__new__(CodexWhipEffects)
    effect._sensor_pose_current = SensorPose(100, 50, 20, 0)
    effect._sensor_pose_updated_at = 100
    effect._sensor_return_remaining = 1.0
    monkeypatch.setattr('codex_whip.effects.time.perf_counter', lambda: 100)
    for i in range(30):
        effect._sensor_pose_target = SensorPose(i*4, 0, 0, .5, True)
        effect._advance_sensor_pose(.3)
    assert effect._sensor_return_remaining == 0
    assert effect._sensor_pose_current.offset_x > 90


def test_sensor_pose_covers_the_entire_codex_window() -> None:
    rectangle = WindowRectangle(100, 50, 1100, 750)
    handle = (200.0, 100.0)
    parked_origin = (400.0, 300.0)

    left_top = sensor_origin_for_window(
        rectangle,
        parked_origin,
        handle,
        SensorPose(-190, 130, 0, 1, True),
    )
    right_bottom = sensor_origin_for_window(
        rectangle,
        parked_origin,
        handle,
        SensorPose(190, -130, 0, 1, True),
    )

    assert (left_top[0] + handle[0], left_top[1] + handle[1]) == pytest.approx(
        (
            rectangle.left,
            rectangle.top,
        )
    )
    assert (
        right_bottom[0] + handle[0],
        right_bottom[1] + handle[1],
    ) == pytest.approx(
        (
            rectangle.right,
            rectangle.bottom,
        )
    )


def test_saved_parking_position_is_clamped_inside_the_window() -> None:
    rectangle = WindowRectangle(100, 50, 1100, 750)
    handle = (200.0, 100.0)

    origin = sensor_origin_for_window(
        rectangle,
        parked_origin=(1200.0, 900.0),
        handle=handle,
        pose=SensorPose(0, 0, 0, 0, False),
    )

    assert (origin[0] + handle[0], origin[1] + handle[1]) == pytest.approx(
        (
            rectangle.right,
            rectangle.bottom,
        )
    )


@pytest.mark.parametrize("offset_x,offset_y,relative_x,relative_y", [
    (0, 0, .5, .5),
    (95, 65, .75, .25),
    (-95, -65, .25, .75),
    (190, -130, 1, 1),
    (-190, 130, 0, 0),
    (1000, -1000, 1, 1),
])
def test_sensor_position_uses_live_window_size_after_resize_and_fullscreen(
    monkeypatch, offset_x, offset_y, relative_x, relative_y,
) -> None:
    from unittest.mock import Mock

    effect = object.__new__(CodexWhipEffects)
    effect._target_hwnd = 123
    effect._manual_armed = False
    effect._animations_enabled = False
    effect._sensor_pose_current = SensorPose(offset_x, offset_y, 0, 1, True)
    effect._parking_position = WhipParkingPosition(.5, .5)
    effect._sync_damage_overlay = Mock()
    effect._move_visual = Mock()
    effect._draw_pose = Mock()
    # Same attached effect, no calibration/reconnect between normal, maximized,
    # fullscreen, and restored windows (including a left-hand monitor).
    for rectangle in (
        WindowRectangle(100, 80, 900, 680),
        WindowRectangle(0, 0, 1920, 1040),
        WindowRectangle(0, 0, 1920, 1080),
        WindowRectangle(-1600, 40, -600, 740),
        WindowRectangle(100, 80, 900, 680),
    ):
        monkeypatch.setattr('codex_whip.effects._window_rectangle', lambda _: rectangle)
        assert effect._sync_position()
        origin = effect._move_visual.call_args.args[0]
        anchor = tuple(origin[i] + effect.IDLE.handle_start[i] for i in (0, 1))
        expected = (rectangle.left + rectangle.width * relative_x,
                    rectangle.top + rectangle.height * relative_y)
        assert anchor == pytest.approx(expected)
        # Gesture direction and displayed handle share the same coordinate map.
        assert effect._sensor_motion_screen_point(effect._sensor_pose_current) == pytest.approx(expected)
        effect._sync_damage_overlay.assert_called_with(rectangle)


def test_damage_direction_tracks_the_sensor_motion_axis(monkeypatch) -> None:
    from codex_whip import effects as effects_module

    effect = object.__new__(CodexWhipEffects)
    effect._target_hwnd = None
    effect._sensor_pose_target = SensorPose(0, 0, 0, 0, False)
    effect._sensor_pose_current = SensorPose(0, 0, 0, 0, False)
    effect._sensor_pose_updated_at = 0.0
    effect._last_sensor_motion_at = 0.0
    effect._sensor_motion_point = None
    effect._sensor_motion_direction = (1.0, 0.0)
    effect._manual_armed = True
    effect._animation_after = None
    times = iter((100.0, 100.05, 100.10))
    monkeypatch.setattr(effects_module.time, "perf_counter", lambda: next(times))

    effect.set_sensor_pose(SensorPose(0, 0, 0, 0.5, True))
    effect.set_sensor_pose(SensorPose(19, -13, 0, 0.7, True))
    effect.set_sensor_pose(SensorPose(38, -26, 0, 0.9, True))

    expected = math.sqrt(0.5)
    assert effect._sensor_motion_direction == pytest.approx(
        (expected, expected),
        abs=0.02,
    )


def test_effect_target_reattaches_only_when_codex_window_changes() -> None:
    window = object.__new__(CodexWhipWindow)
    from codex_whip.settings import Settings
    window.settings = Settings()
    window._effect_target_handle = None
    window.effects = FakeEffects()
    logs: list[str] = []
    window._append_log = logs.append

    window._apply_effect_target(True, {"handle": 100}, automatic=True)
    window._apply_effect_target(True, {"handle": 100}, automatic=True)
    window._apply_effect_target(False, "not found", automatic=True)

    assert window.effects.attached == [100]
    assert window.effects.detached == 1
    assert window._effect_target_handle is None
    assert any("自动显示" in line for line in logs)
    assert any("同步隐藏" in line for line in logs)


def test_constrained_cartoon_whip_follows_handle_and_keeps_segment_lengths() -> None:
    physics = CartoonWhipPhysics((400, 200))
    for _ in range(45):
        pose = physics.step((520, 260), 1 / 60, aim_offset_degrees=14)

    assert pose.handle_start[0] > 500
    assert pose.handle_start[1] > 250
    lengths = [math.dist(first, second) for first, second in zip(pose.cord, pose.cord[1:])]
    targets = [physics._segment_length(index) for index in range(len(lengths))]
    assert max(abs(length - target) for length, target in zip(lengths, targets)) < 1.6


def test_black_whip_rope_is_exactly_fifty_percent_longer() -> None:
    source = WhipPose(
        (100.0, 20.0),
        (80.0, 40.0),
        ((80.0, 40.0), (60.0, 50.0), (30.0, 70.0)),
    )
    extended = extend_whip_pose(source, 1.5, x_offset=190.0)

    source_length = sum(math.dist(a, b) for a, b in zip(source.cord, source.cord[1:]))
    extended_length = sum(
        math.dist(a, b) for a, b in zip(extended.cord, extended.cord[1:])
    )
    assert extended_length == pytest.approx(source_length * 1.5)
    assert CartoonWhipPhysics.CORD_LENGTH_MULTIPLIER == pytest.approx(1.5)
    assert CartoonWhipPhysics.CORD_WEIGHT_MULTIPLIER == pytest.approx(1.2)
    assert CartoonWhipPhysics.INERTIAL_DAMPING == pytest.approx(0.80)
    assert CartoonWhipPhysics.CORD_ANCHOR_FOLLOW == pytest.approx(0.40)
    assert CartoonWhipPhysics.VELOCITY_SLEEP_THRESHOLD == pytest.approx(0.065)


def test_rope_anchor_follow_reduces_the_lingering_swing_envelope() -> None:
    physics = CartoonWhipPhysics((500, 100))
    for index in range(30):
        physics.step(
            (500 + index * 4, 100 + index * 2),
            1 / 60,
            aim_offset_degrees=12,
        )

    tips = [
        physics.step((620, 160), 1 / 60, aim_offset_degrees=12).cord[-1]
        for _ in range(120)
    ]
    second_half_second = tips[30:60]
    fourth_half_second = tips[90:120]

    assert max(x for x, _y in second_half_second) - min(
        x for x, _y in second_half_second
    ) < 46.0
    assert max(x for x, _y in fourth_half_second) - min(
        x for x, _y in fourth_half_second
    ) < 15.5


def test_weighted_rope_goes_to_sleep_under_a_stationary_handle() -> None:
    physics = CartoonWhipPhysics((500, 100))
    initial_tip = physics.points[-1]
    assert initial_tip.y > physics._handle_end()[1]
    tips = [physics.step((500, 100), 1 / 60).cord[-1] for _ in range(1000)]
    final_drift = max(
        math.dist(first, second)
        for first, second in zip(tips[-60:], tips[-59:])
    )

    assert final_drift < 0.01


def test_stationary_anchor_allows_rope_to_settle_instead_of_locking_it() -> None:
    physics = CartoonWhipPhysics((500, 100))
    for index in range(30):
        physics.step((500 + index * 4, 100 + index * 2), 1 / 60, aim_offset_degrees=12)

    first = physics.step((620, 160), 1 / 60, aim_offset_degrees=12)
    second = physics.step((620, 160), 1 / 60, aim_offset_degrees=12)
    assert math.dist(first.cord[-1], second.cord[-1]) > 0.01

    tips = [
        physics.step((620, 160), 1 / 60, aim_offset_degrees=12).cord[-1]
        for _ in range(1000)
    ]
    final_drift = max(
        math.dist(first_tip, second_tip)
        for first_tip, second_tip in zip(tips[-60:], tips[-59:])
    )

    assert final_drift < 0.01


def test_sensor_angle_changes_dynamic_automatic_strike_tip() -> None:
    current = CartoonWhipPhysics((500, 300)).step((500, 300), 1 / 60)

    left = object.__new__(CodexWhipEffects)
    left._current_pose = current
    left._sensor_pose_current = SensorPose(0, 0, -24, 0.5, True)
    left._set_sensor_animation_poses()

    right = object.__new__(CodexWhipEffects)
    right._current_pose = current
    right._sensor_pose_current = SensorPose(0, 0, 24, 0.5, True)
    right._set_sensor_animation_poses()

    assert left._animation_start_pose is current
    assert right._animation_start_pose is current
    assert len(left._animation_strike_pose.cord) == len(current.cord)
    assert left._animation_strike_pose.handle_start == pytest.approx(current.handle_start)
    assert right._animation_strike_pose.handle_start == pytest.approx(current.handle_start)
    assert math.dist(
        left._animation_strike_pose.cord[-1],
        right._animation_strike_pose.cord[-1],
    ) > 100.0


def test_dynamic_strike_tip_is_fitted_inside_codex_without_moving_handle() -> None:
    rectangle = WindowRectangle(100, 80, 900, 680)
    pose = WhipPose(
        (600.0, 300.0),
        (540.0, 360.0),
        ((540.0, 360.0), (200.0, 740.0), (-120.0, 980.0)),
    )

    fitted = fit_pose_tip_to_window(pose, (20.0, 30.0), rectangle)
    screen_tip = (
        20.0 + fitted.cord[-1][0],
        30.0 + fitted.cord[-1][1],
    )

    assert fitted.handle_start == pose.handle_start
    assert rectangle.left + 28 <= screen_tip[0] <= rectangle.right - 28
    assert rectangle.top + 28 <= screen_tip[1] <= rectangle.bottom - 28


def test_automatic_strike_records_the_same_tip_that_is_drawn(monkeypatch) -> None:
    from codex_whip import effects as effects_module

    rectangle = WindowRectangle(100, 80, 900, 680)
    effect = object.__new__(CodexWhipEffects)
    effect._target_hwnd = 101
    effect._manual_armed = False
    effect._target_available = lambda: True
    effect._cancel_animation = lambda: None
    effect._sync_position = lambda: True
    effect._visual_origin = (20.0, 30.0)
    effect._current_pose = CodexWhipEffects.IDLE
    effect._sensor_pose_current = SensorPose(0, 0, 0, 0, False)
    effect._sensor_motion_direction = (0.25, -0.75)
    effect._animations_enabled = False
    effect.hit_window = SimpleNamespace(withdraw=lambda: None)
    effect._show_impact = lambda _point: None
    effect._play_sound = lambda _sound: True
    effect._crack_sound = b"sound"
    effect._start_shake = lambda: None
    effect._root = SimpleNamespace(after=lambda _delay, _callback: "after-id")
    drawn: list[WhipPose] = []
    recorded: list[tuple[tuple[float, float], tuple[float, float]]] = []
    effect._draw_pose = drawn.append
    effect._record_damage = lambda point, direction=(1.0, 0.0): recorded.append(
        (point, direction)
    )
    monkeypatch.setattr(effects_module, "_window_rectangle", lambda _hwnd: rectangle)

    assert effect.play() is True

    drawn_tip = (
        effect._visual_origin[0] + drawn[-1].cord[-1][0],
        effect._visual_origin[1] + drawn[-1].cord[-1][1],
    )
    assert recorded[0][0] == pytest.approx(drawn_tip)
    assert recorded[0][1] == pytest.approx((0.25, -0.75))
    assert rectangle.left + 28 <= drawn_tip[0] <= rectangle.right - 28
    assert rectangle.top + 28 <= drawn_tip[1] <= rectangle.bottom - 28


def test_mouse_strike_keeps_the_requested_screen_impact() -> None:
    effect = object.__new__(CodexWhipEffects)
    effect._target_available = lambda: True
    effect._cancel_animation = lambda: None
    effect._animations_enabled = False
    effect._manual_motion_direction = (0.0, 1.0)
    effect._move_visual = lambda origin: setattr(effect, "_visual_origin", origin)
    effect.hit_window = SimpleNamespace(withdraw=lambda: None)
    effect.window = SimpleNamespace(winfo_viewable=lambda: True)
    effect._raise_visual = lambda: None
    drawn: list[WhipPose] = []
    impacts: list[tuple[float, float]] = []
    recorded: list[tuple[tuple[float, float], tuple[float, float]]] = []
    effect._draw_pose = drawn.append
    effect._show_impact = impacts.append
    effect._play_sound = lambda _sound: True
    effect._crack_sound = b"sound"
    effect._record_damage = lambda point, direction=(1.0, 0.0): recorded.append(
        (point, direction)
    )
    effect._start_shake = lambda: None
    effect._root = SimpleNamespace(after=lambda _delay, _callback: "after-id")

    screen_point = (640.0, 360.0)
    assert effect.play_at(screen_point) is True

    assert drawn[-1] is CodexWhipEffects.STRIKE
    assert recorded[0][0] == screen_point
    assert recorded[0][1] == pytest.approx((0.0, 1.0))
    assert (
        effect._visual_origin[0] + impacts[-1][0],
        effect._visual_origin[1] + impacts[-1][1],
    ) == pytest.approx(screen_point)


def test_catmull_rom_smoothing_preserves_endpoints() -> None:
    points = ((0.0, 0.0), (20.0, 12.0), (40.0, -4.0), (60.0, 8.0))
    smoothed = catmull_rom_points(points, 5)

    assert smoothed[0] == points[0]
    assert smoothed[-1] == pytest.approx(points[-1])
    assert len(smoothed) == 16


def test_damage_mask_and_patch_reveal_real_raster_content() -> None:
    mask = build_tear_mask((320, 120), seed=7)
    surface = Image.new("RGB", (320, 120), "#12161c")
    pcb = Image.new("RGB", (320, 120), "#0b6b3a")
    patch = build_damage_patch(surface, pcb, seed=7)

    assert mask.getbbox() is not None
    assert patch.mode == "RGBA"
    assert patch.getchannel("A").getbbox() is not None
    assert max(patch.getchannel("G").getextrema()) > 50


def test_tear_long_axis_follows_the_whip_tip_direction() -> None:
    vertical = build_directional_tear_mask((320, 120), seed=7, direction=(0, 1))
    diagonal = build_directional_tear_mask((320, 120), seed=7, direction=(1, 1))
    points = [
        (x, y)
        for y in range(diagonal.height)
        for x in range(diagonal.width)
        if diagonal.getpixel((x, y)) > 100
    ]
    mean_x = sum(x for x, _y in points) / len(points)
    mean_y = sum(y for _x, y in points) / len(points)
    covariance = sum(
        (x - mean_x) * (y - mean_y) for x, y in points
    ) / len(points)

    assert vertical.height > vertical.width
    assert covariance > 0


def test_nearby_damage_masks_merge_but_distant_damage_stays_separate() -> None:
    wound = Image.new("L", (40, 24), 255)
    nearby = [(10, 20, wound), (62, 20, wound), (190, 20, wound)]

    groups = group_damage_masks(nearby, proximity=12)
    merged = merge_damage_masks((130, 70), nearby[:2], proximity=12)

    assert sorted(len(group) for group in groups) == [1, 2]
    assert merged.getpixel((56, 32)) > 0


def test_fixed_pcb_backplane_keeps_one_window_coordinate_system() -> None:
    pcb = Image.new("RGB", (240, 120))
    pixels = pcb.load()
    for y in range(pcb.height):
        for x in range(pcb.width):
            pixels[x, y] = (x, min(255, x + 5), y)
    backplane = cover_image(pcb, (200, 100), overscan=1.0)
    mask = Image.new("L", (200, 100), 0)
    mask.paste(255, (20, 20, 60, 50))
    mask.paste(255, (140, 50, 180, 80))

    layer = build_damage_layer(backplane, mask, seed=31)

    assert layer.getpixel((40, 35))[:3] == backplane.getpixel((40, 35))
    assert layer.getpixel((160, 65))[:3] == backplane.getpixel((160, 65))
    assert layer.getpixel((100, 50))[3] == 0


def test_damage_layer_has_no_decorative_border_outside_the_tear() -> None:
    backplane = Image.new("RGB", (220, 100), "#14824b")
    mask = Image.new("L", (220, 100), 0)
    mask.paste(255, (60, 35, 160, 65))

    layer = build_damage_layer(backplane, mask, seed=23)
    visible_edge_pixels = [
        layer.getpixel((x, y))
        for y in range(layer.height)
        for x in range(layer.width)
        if mask.getpixel((x, y)) == 0 and layer.getpixel((x, y))[3] > 0
    ]

    assert visible_edge_pixels == []


def test_damage_waits_three_quiet_seconds_before_healing_starts() -> None:
    assert healing_visibility(0.0) == pytest.approx(0.0)
    assert healing_visibility(0.14) == pytest.approx(1.0)
    assert healing_visibility(10.0, 3.0) == pytest.approx(1.0)
    assert 0.0 < healing_visibility(10.0, 3.4) < 1.0
    assert healing_visibility(10.0, 3.85) == pytest.approx(0.0)
    assert healing_visibility(10.0, 0.1) == pytest.approx(1.0)


def test_record_damage_keeps_every_active_wound(monkeypatch) -> None:
    from codex_whip import effects as effects_module

    effect = object.__new__(CodexWhipEffects)
    effect._target_hwnd = 101
    effect._damage_random = SimpleNamespace(randrange=lambda *_args: 17)
    effect._damage_items = []
    effect._last_damage_strike_at = 0.0
    effect._sync_damage_overlay = lambda _rectangle: None
    monkeypatch.setattr(
        effects_module,
        "_window_rectangle",
        lambda _handle: WindowRectangle(0, 0, 1200, 800),
    )

    for index in range(12):
        effect._record_damage((120 + index * 70, 300))

    assert len(effect._damage_items) == 12


def test_damage_frequency_opens_a_wound_on_each_configured_nth_strike(
    monkeypatch,
) -> None:
    from codex_whip import effects as effects_module

    effect = object.__new__(CodexWhipEffects)
    effect._damage_interval = 3
    effect._damage_strike_count = 0
    effect._last_damage_strike_at = 0.0
    recorded: list[tuple[tuple[int, int], tuple[float, float]]] = []
    effect._record_damage = lambda point, direction=(1.0, 0.0): recorded.append(
        (point, direction)
    )
    timestamps = iter(float(index) for index in range(1, 8))
    monkeypatch.setattr(effects_module.time, "perf_counter", lambda: next(timestamps))

    results = [effect._maybe_record_damage((index, 20)) for index in range(1, 8)]

    assert results == [False, False, True, False, False, True, False]
    assert [item[0] for item in recorded] == [(3, 20), (6, 20)]
    assert effect._damage_strike_count == 7
    assert effect._last_damage_strike_at == 7.0


def test_changing_damage_frequency_restarts_the_strike_counter() -> None:
    effect = object.__new__(CodexWhipEffects)
    effect._damage_interval = 5
    effect._damage_strike_count = 4

    effect.set_damage_interval(2)

    assert effect._damage_interval == 2
    assert effect._damage_strike_count == 0


def test_disabled_wounds_and_zero_interval():
    effect = object.__new__(CodexWhipEffects)
    effect.set_damage_interval(0)
    recorded = []
    effect._record_damage = lambda *args: recorded.append(args)
    effect.wounds_enabled = False
    assert not effect._maybe_record_damage((0,0))
    assert recorded == []
    effect.wounds_enabled = True
    assert effect._maybe_record_damage((0,0))
    assert effect._maybe_record_damage((0,0))
    assert len(recorded) == 2


def test_generated_pcb_asset_is_bundled() -> None:
    pcb = Image.open(bundled_asset_path("visual/pcb-photoreal-v1.png"))

    assert pcb.width >= 1000 and pcb.height >= 1000


def test_screen_pose_only_recenters_after_stream_loss(monkeypatch) -> None:
    from codex_whip import effects as effects_module

    effect = object.__new__(CodexWhipEffects)
    effect._sensor_pose_target = SensorPose(40, 20, 12, 0.2, False)
    effect._sensor_pose_current = SensorPose(40, 20, 12, 0.2, False)
    effect._sensor_pose_updated_at = 102.8
    effect._last_sensor_motion_at = 100.0
    monkeypatch.setattr(effects_module.time, "perf_counter", lambda: 102.9)

    effect._advance_sensor_pose(0.5)
    assert effect._sensor_pose_current.offset_x == pytest.approx(40)

    monkeypatch.setattr(effects_module.time, "perf_counter", lambda: 103.1)
    effect._advance_sensor_pose(0.5)
    assert effect._sensor_pose_current.offset_x == pytest.approx(40)

    monkeypatch.setattr(effects_module.time, "perf_counter", lambda: 106.0)
    effect._advance_sensor_pose(0.5)
    assert effect._sensor_pose_current.offset_x == pytest.approx(20)


def test_faster_refresh_preserves_follow_response(monkeypatch):
    from codex_whip import effects as module
    monkeypatch.setattr(module.time, "perf_counter", lambda: 100.0)
    def effect():
        result = object.__new__(CodexWhipEffects)
        result._sensor_pose_target = SensorPose(40, 20, 12, .2, True)
        result._sensor_pose_current = SensorPose(0, 0, 0, 0)
        result._sensor_pose_updated_at = 100.0
        return result
    old, new = effect(), effect()
    old._advance_sensor_pose(.30)
    for _ in range(3):
        new._advance_sensor_pose(1 - .70 ** (1 / 3))
    assert new._sensor_pose_current.offset_x == pytest.approx(old._sensor_pose_current.offset_x)
    assert new._sensor_pose_current.angle_degrees == pytest.approx(old._sensor_pose_current.angle_degrees)


def test_sync_scheduler_subtracts_render_work_and_never_bursts(monkeypatch):
    from codex_whip import effects as module
    from unittest.mock import Mock
    effect = object.__new__(CodexWhipEffects)
    effect._root = Mock()
    effect._sync_started_at = 100.0
    monkeypatch.setattr(module.time, "perf_counter", lambda: 100.006)
    effect._schedule_sync()
    delay = effect._root.after.call_args.args[0]
    assert 9 <= delay <= 11
    monkeypatch.setattr(module.time, "perf_counter", lambda: 101.0)
    effect._schedule_sync()
    assert effect._root.after.call_args.args[0] == 1


def test_stationary_sensor_update_keeps_final_attitude_target(monkeypatch) -> None:
    from codex_whip import effects as effects_module

    effect = object.__new__(CodexWhipEffects)
    effect._sensor_pose_target = SensorPose(40, 20, 12, 0.2, True)
    effect._sensor_pose_current = SensorPose(36, 18, 10, 0.1, True)
    effect._sensor_pose_updated_at = 100.0
    effect._last_sensor_motion_at = 100.0
    effect._manual_armed = False
    effect._animation_after = None
    effect._target_available = lambda: False
    monkeypatch.setattr(effects_module.time, "perf_counter", lambda: 101.0)

    effect.set_sensor_pose(SensorPose(41, 22, 13, 0.01, False))

    assert effect._sensor_pose_target == SensorPose(41, 22, 13, 0.01, False)


def test_sensor_notification_burst_only_updates_target(monkeypatch):
    from codex_whip import effects as module
    from unittest.mock import Mock
    effect = object.__new__(CodexWhipEffects)
    effect._sensor_pose_current = original = SensorPose(0, 0, 0, 0)
    effect._track_sensor_motion_axis = Mock()
    effect._advance_sensor_pose = Mock()
    effect._sync_position = Mock()
    effect._show_idle_hitbox = Mock()
    monkeypatch.setattr(module.time, 'perf_counter', lambda: 100.)
    for x in (10, 20, 30, 40):
        effect.set_sensor_pose(SensorPose(x, x/2, 3, .2, True))
    assert effect._sensor_pose_current is original
    assert effect._sensor_pose_target.offset_x == 40
    assert effect._sensor_pose_updated_at == 100.
    assert effect._track_sensor_motion_axis.call_count == 4
    effect._advance_sensor_pose.assert_not_called()
    effect._sync_position.assert_not_called()
    effect._show_idle_hitbox.assert_not_called()


def test_sync_frame_advances_pose_once_without_duplicate_draw(monkeypatch):
    from codex_whip import effects as module
    from unittest.mock import Mock
    effect = object.__new__(CodexWhipEffects)
    effect._sync_started_at = 100.
    effect._target_hwnd = 1
    effect._target_available = lambda: True
    effect._scare_active = effect._manual_armed = effect._dragging = False
    effect._animation_after = None
    effect._advance_sensor_pose = Mock()
    effect._sync_position = Mock()
    effect._show_idle_hitbox = Mock()
    effect._schedule_sync = Mock()
    effect.window = Mock()
    effect.window.winfo_viewable.return_value = True
    effect._whip_drawing = Mock()
    effect._presentation = Mock(owns_geometry=False)
    monkeypatch.setattr(module.time, 'perf_counter', lambda: 100.016)
    monkeypatch.setattr(module, '_cursor_position', lambda: (0, 0))
    effect._sync_tick()
    effect._advance_sensor_pose.assert_called_once()
    assert effect._advance_sensor_pose.call_args.args[0] == pytest.approx(1-math.exp(-.016/.060))
    effect._sync_position.assert_called_once()
    effect._whip_drawing.draw.assert_not_called()
    effect._presentation.render.assert_called_once()


def test_kinematic_grip_stops_exactly_while_rope_keeps_settling():
    physics = CartoonWhipPhysics((500, 100))
    for index in range(30):
        physics.step((500 + index * 5, 100 + index * 2), kinematic=True)
    first = physics.step((650, 160), aim_offset_degrees=15, kinematic=True)
    second = physics.step((650, 160), aim_offset_degrees=15, kinematic=True)
    assert first.handle_start == second.handle_start == (650, 160)
    assert first.handle_end == second.handle_end
    assert first.cord[-1] != second.cord[-1]
    tips = [physics.step((650, 160), aim_offset_degrees=15, kinematic=True).cord[-1]
            for _ in range(480)]
    assert max(math.dist(tips[-1], point) for point in tips[-30:]) < 0.5


def test_kinematic_physics_substeps_match_regular_frame_rate():
    slow = CartoonWhipPhysics((500, 100))
    fast = CartoonWhipPhysics((500, 100))
    for _ in range(60):
        slow_pose = slow.step((520, 140), 1/20, kinematic=True)
        for _ in range(3):
            fast_pose = fast.step((520, 140), 1/60, kinematic=True)
    assert math.dist(slow_pose.cord[-1], fast_pose.cord[-1]) < 0.1


def test_rope_gravity_is_independent_of_small_or_jittery_render_steps():
    normal = CartoonWhipPhysics((500, 100))
    tiny = CartoonWhipPhysics((500, 100))
    jittery = CartoonWhipPhysics((500, 100))
    for _ in range(120):
        normal.step((500, 100), 1/60, kinematic=True)
    for _ in range(250):
        tiny.step((500, 100), 0.008, kinematic=True)
    for _ in range(50):
        jittery.step((500, 100), 0.003, kinematic=True)
        jittery.step((500, 100), 0.037, kinematic=True)
    assert tiny.pose().cord == normal.pose().cord == jittery.pose().cord


def test_manual_stroke_moves_cord_and_hits_with_tip_not_rod():
    physics = CartoonWhipPhysics((600, 350), scale=0.58)
    stroke = ManualWhipStroke(physics, (600, 350))
    start = stroke.sample(0, (600, 350))
    windup = stroke.sample(0.065, (600, 350))
    contact = stroke.sample(stroke.IMPACT_SECONDS, (900, 600))
    assert start == physics.pose()
    assert contact.cord[-1] == pytest.approx((600, 350), abs=0.01)
    assert math.dist(contact.handle_end, contact.cord[-1]) > 140
    assert math.dist(windup.cord[-1], contact.cord[-1]) > 150
    # The rod stays rigid, including half-way through the swing.
    for ms in range(0, 281, 8):
        pose = stroke.sample(ms/1000, (600, 350))
        assert math.dist(pose.handle_start, pose.handle_end) == pytest.approx(physics.handle_length)
        assert pose.cord[0] == pytest.approx(pose.handle_end)
    end = stroke.sample(0.280, (700, 400))
    physics.adopt_pose(end)
    assert physics.pose().cord == end.cord
    assert end.handle_start == pytest.approx((700, 400), abs=0.01)
    assert math.dist(physics.step((700, 400)).cord[-1], end.cord[-1]) > 0.1


def test_manual_physics_frame_draws_contact_before_damage_even_when_frame_is_late(monkeypatch):
    from codex_whip import effects as module
    effect = object.__new__(CodexWhipEffects)
    effect._manual_armed = True
    effect._physics = CartoonWhipPhysics((600, 350), scale=0.58)
    effect._manual_stroke = ManualWhipStroke(effect._physics, (600, 350))
    effect._manual_target = effect._manual_strike_impact = (600, 350)
    effect._manual_strike_started_at = effect._physics_last_time = 10.0
    effect._manual_strike_fired = False
    effect._pending_damage_direction = (1, 0)
    effect._root = SimpleNamespace(after=lambda *args: 'timer')
    effect._crack_sound = b''
    damage, drawn, origins, sounds = [], [], [], []
    effect._play_sound = sounds.append
    effect._maybe_record_damage = lambda point, direction: damage.append(point)
    effect._start_shake = effect._hide_impact = effect._raise_visual = lambda: None
    effect._move_visual = origins.append
    effect._draw_pose = drawn.append
    effect._show_impact = lambda point: None
    monkeypatch.setattr(module, '_cursor_position', lambda: (800, 400))
    monkeypatch.setattr(module.time, 'perf_counter', lambda: 10.175)
    effect._physics_frame()
    tip = tuple(origins[-1][i]+drawn[-1].cord[-1][i] for i in range(2))
    assert tip == pytest.approx(damage[0])
    effect._physics_frame()
    assert len(damage) == len(sounds) == 1


def test_settings_blocks_all_overlay_reposition_paths():
    effects = object.__new__(CodexWhipEffects)
    effects._settings_open = True
    # No OS handles are needed: every route must exit before native drawing.
    assert effects._sync_position() is False
    assert effects._move_visual((1, 2)) is None
    assert effects._raise_visual() is None
    assert effects.play() is False
