import math

import pytest

from codex_whip.models import RawMotionBatch, RawMotionFrame
from codex_whip.sensor_pose import SensorPoseTracker


def frame(t, gx=0, gy=0, gz=0, ax=0, ay=1, az=0):
    return RawMotionFrame(t, gx, gy, gz, ax, ay, az)


def feed(tracker, frames, *, auto_center=False):
    return tracker.feed_batch(RawMotionBatch(1, frames[0].timestamp_ms, tuple(frames)),
                              auto_center=auto_center)


def test_stationary_sensor_stays_centered():
    tracker = SensorPoseTracker()
    pose = feed(tracker, [frame(t, gx=0.1, gy=-0.1) for t in range(0, 10000, 10)])
    assert pose.offset_x == 0
    assert pose.offset_y == 0
    assert not pose.moving


def test_slow_three_dps_turn_survives_and_maps_to_angle():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0)])
    pose = feed(tracker, [frame(t, gy=3) for t in range(10, 2010, 10)])
    assert pose.offset_x == pytest.approx(6 / 25 * 190, abs=0.5)
    assert abs(pose.offset_y) < 0.1
    assert pose.moving


def test_held_angle_does_not_recenter_after_three_seconds():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0)])
    feed(tracker, [frame(t, gy=15) for t in range(10, 1010, 10)])
    settled = feed(tracker, [frame(t) for t in range(1010, 1110, 10)])
    held = feed(tracker, [frame(t) for t in range(1110, 11000, 10)])
    assert held.offset_x == pytest.approx(settled.offset_x, abs=1e-6)
    assert held.offset_x > 100
    assert not held.moving


@pytest.mark.parametrize('up', [(0, 1, 0), (0, 0, 1), (0, -1, 0), (0, 2**-0.5, 2**-0.5), (1, 0, 0)])
def test_yaw_is_horizontal_in_the_calibrated_grip_frame(up):
    tracker = SensorPoseTracker()
    feed(tracker, [frame(t, ax=up[0], ay=up[1], az=up[2]) for t in range(0, 610, 10)])
    assert tracker.calibrate_neutral() is not None
    pose = feed(tracker, [frame(t, gx=up[0]*10, gy=up[1]*10, gz=up[2]*10,
                                ax=up[0], ay=up[1], az=up[2]) for t in range(610, 1610, 10)])
    assert pose.offset_x == pytest.approx(10 / 25 * 190, abs=1.2)
    assert abs(pose.offset_y) < 0.1


def test_upward_pointing_is_positive_before_screen_y_inversion():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0)])
    frames = []
    for t in range(10, 1010, 10):
        angle = math.radians(10 * t / 1000)
        frames.append(frame(t, gx=-10, ay=math.cos(angle), az=math.sin(angle)))
    pose = feed(tracker, frames)
    assert pose.offset_y == pytest.approx(10 / 20 * 130, abs=1.5)
    assert abs(pose.offset_x) < 0.1


def test_calibration_uses_stationary_window_and_cancels_bias():
    tracker = SensorPoseTracker()
    assert tracker.calibrate_neutral() is None
    feed(tracker, [frame(t, gy=0.7, gx=0.2) for t in range(0, 610, 10)])
    assert tracker.calibrate_neutral().offset_x == 0
    pose = feed(tracker, [frame(t, gy=0.7, gx=0.2) for t in range(610, 10000, 10)])
    assert abs(pose.offset_x) < 0.01
    assert abs(pose.offset_y) < 0.01


def test_moving_calibration_is_rejected():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(t, gy=60) for t in range(0, 610, 10)])
    assert tracker.calibrate_neutral() is None


def test_linear_acceleration_never_becomes_screen_translation():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0)])
    pose = feed(tracker, [frame(t, ax=1.5 * math.sin(t / 60), ay=1, az=0.3)
                          for t in range(10, 1010, 10)])
    assert pose.offset_x == 0
    assert pose.offset_y == 0
    assert not pose.moving


def test_single_gyro_spike_is_removed_without_losing_slow_turns():
    tracker = SensorPoseTracker()
    pose = feed(tracker, [frame(0), frame(10), frame(20, gx=1800), frame(30), frame(40)])
    assert not pose.moving
    assert pose.offset_x == 0
    assert pose.offset_y == 0


def test_duplicate_and_reordered_frames_do_not_integrate_twice():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0), frame(10, gy=100), frame(20, gy=100)])
    before = feed(tracker, [frame(30, gy=100)])
    after = feed(tracker, [frame(20, gy=100), frame(30, gy=100)])
    assert after.offset_x == before.offset_x


def test_short_gap_holds_pose_instead_of_teleporting_or_integrating_missing_motion():
    tracker = SensorPoseTracker()
    before = feed(tracker, [frame(0), frame(10, gy=100), frame(20, gy=100)])
    pose = feed(tracker, [frame(2000)])
    assert pose.offset_x == before.offset_x
    assert not pose.moving


@pytest.mark.parametrize('axis', ['yaw', 'pitch'])
def test_wide_turn_has_no_ninety_degree_pole_or_180_degree_flip(axis):
    tracker = SensorPoseTracker()
    previous = feed(tracker, [frame(0)])
    for t in range(10, 21010, 10):
        a = math.radians(t / 100)
        sample = (frame(t, gy=10) if axis == 'yaw'
                  else frame(t, gx=-10, ay=math.cos(a), az=math.sin(a)))
        pose = feed(tracker, [sample])
        assert abs(pose.offset_x-previous.offset_x) < 2
        assert abs(pose.offset_y-previous.offset_y) < 2
        assert abs(pose.angle_degrees) < 0.01  # No false wrist twist.
        previous = pose
    assert (pose.offset_x if axis == 'yaw' else pose.offset_y) > 0


def test_turn_past_180_then_return_recovers_the_same_point():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0)])
    feed(tracker, [frame(t, gy=100) for t in range(10, 2010, 10)])
    feed(tracker, [frame(t, gy=-100) for t in range(2010, 4010, 10)])
    pose = feed(tracker, [frame(t) for t in range(4010, 4110, 10)])
    assert abs(pose.offset_x) < 0.1


def test_long_disconnect_rebases_without_integrating_absent_motion():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0), frame(10, gy=100), frame(20, gy=100)])
    assert feed(tracker, [frame(4000)]).offset_x == 0


def test_timestamp_rollover_keeps_attitude_continuous():
    tracker = SensorPoseTracker()
    pose = feed(tracker, [frame(t & 0xFFFFFFFF, gy=10) for t in range(0xFFFFFFD0, 0x100000030, 10)])
    assert 0 < pose.offset_x < 10
    assert math.sqrt(sum(v*v for v in tracker._q)) == pytest.approx(1)


def test_output_clamps_to_existing_overlay_limits():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0)])
    pose = feed(tracker, [frame(t, gy=60) for t in range(10, 1010, 10)])
    assert pose.offset_x == SensorPoseTracker.MAX_OFFSET_X_PX == 190
    assert abs(pose.offset_y) <= 130
    assert abs(pose.angle_degrees) <= 42


@pytest.mark.parametrize('phase', [0, .3, 1.5])
def test_bounded_hand_tremor_can_recenter_without_changing_gyro_bias(phase):
    tracker = SensorPoseTracker()
    feed(tracker, [frame(t, gy=.7, gx=.2) for t in range(0, 610, 10)])
    tracker.calibrate_neutral()
    bias = tracker.gyro_bias
    feed(tracker, [frame(t, gx=.2, gy=.7 + 12 * math.sin(t / 1000 * math.tau * 6 + phase),
                        ax=.02 * math.sin(t / 40), ay=1 + .015 * math.cos(t / 40))
                   for t in range(610, 1610, 10)])
    assert tracker.grip_stability().ready
    assert not tracker.grip_stability().bias_quiet
    assert tracker.calibrate_neutral() is not None
    assert tracker.gyro_bias == bias


@pytest.mark.parametrize('rate', [3, 6, 20, 90])
def test_deliberate_continuous_turn_is_not_a_stable_grip(rate):
    tracker = SensorPoseTracker()
    feed(tracker, [frame(t, gy=rate) for t in range(0, 610, 10)])
    assert not tracker.grip_stability().ready
    assert tracker.calibrate_neutral() is None
    assert tracker.gyro_bias == (0, 0, 0)


def test_a_slow_real_turn_is_not_learned_as_gyro_bias():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(t, gy=1.8) for t in range(0, 610, 10)])
    assert tracker.calibrate_neutral() is not None  # <1 degree over window.
    assert tracker.gyro_bias == (0, 0, 0)
    pose = feed(tracker, [frame(t, gy=1.8) for t in range(610, 1610, 10)])
    assert pose.offset_x > 10


def test_hold_still_rejects_impact_and_short_or_discontinuous_windows():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(t) for t in range(0, 210, 10)])
    assert not tracker.grip_stability().ready
    feed(tracker, [frame(t, ay=2 if t == 530 else 1) for t in range(210, 610, 10)])
    assert not tracker.grip_stability().ready
    feed(tracker, [frame(t) for t in range(610, 1100, 10)])
    assert tracker.grip_stability().ready
    feed(tracker, [frame(1240)])  # 150ms hole, not enough to reset pose.
    assert not tracker.grip_stability().ready


def test_manual_recenter_never_learns_motion_as_bias():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(t, gy=20) for t in range(0, 610, 10)])
    assert not tracker.grip_stability().ready
    assert tracker.orientation_reading().ready
    orientation = tracker.orientation
    assert tracker.calibrate_neutral(allow_motion=True) is not None
    assert tracker.orientation == orientation
    assert tracker.gyro_bias == (0, 0, 0)
    assert feed(tracker, [frame(t, gy=20) for t in range(610, 1110, 10)]).offset_x > 70


def test_manual_snapshot_keeps_transport_and_saturation_guards():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0)])
    assert tracker.calibrate_neutral(allow_motion=True) is None
    feed(tracker, [frame(t, gy=1950) for t in range(10, 610, 10)])
    assert tracker.calibrate_neutral(allow_motion=True) is None
    feed(tracker, [frame(t) for t in range(610, 1100, 10)])
    assert tracker.orientation_reading().ready
    feed(tracker, [frame(1240)])
    assert not tracker.orientation_reading().ready


def idle_setup():
    tracker = SensorPoseTracker()
    feed(tracker, [frame(0)], auto_center=True)
    feed(tracker, [frame(t, gy=30) for t in range(10, 1010, 10)], auto_center=True)
    return tracker


def test_auto_center_waits_three_seconds_then_rebases_without_snap_back():
    tracker = idle_setup()
    before = feed(tracker, [frame(t) for t in range(1010, 4000, 10)], auto_center=True)
    assert before.offset_x > 180 and not before.auto_centered
    centered = feed(tracker, [frame(t) for t in range(4000, 4100, 10)], auto_center=True)
    assert centered.auto_centered
    assert centered.offset_x == pytest.approx(0, abs=.01)
    next_pose = feed(tracker, [frame(t, gy=10) for t in range(4100, 5100, 10)], auto_center=True)
    assert 70 < next_pose.offset_x < 80  # Relative to new zero, not the old 30°.
    assert not next_pose.auto_centered
    assert tracker.gyro_bias == (0, 0, 0)


@pytest.mark.parametrize('phase', [0, .5, 1.5])
def test_auto_center_accepts_hand_tremor_and_small_accel_noise(phase):
    tracker = idle_setup()
    pose = feed(tracker, [frame(t, gy=11 * math.sin(t / 1000 * math.tau * 6 + phase),
                                ax=.06 * math.sin(t / 50), ay=1 + .05 * math.cos(t / 50))
                          for t in range(1010, 4310, 10)], auto_center=True)
    assert pose.auto_centered
    assert abs(pose.offset_x) < 6
    assert tracker.gyro_bias == (0, 0, 0)


@pytest.mark.parametrize('rate', [1.5, 3, 8, 30])
def test_continuous_slow_turn_is_not_idle(rate):
    tracker = idle_setup()
    pose = feed(tracker, [frame(t, gy=rate) for t in range(1010, 13010, 10)], auto_center=True)
    assert not pose.auto_centered
    assert pose.offset_x > 180


def test_motion_interrupts_idle_countdown():
    tracker = idle_setup()
    feed(tracker, [frame(t) for t in range(1010, 3500, 10)], auto_center=True)
    feed(tracker, [frame(t, gy=40) for t in range(3500, 4000, 10)], auto_center=True)
    assert not feed(tracker, [frame(t) for t in range(4000, 6900, 10)], auto_center=True).auto_centered
    assert feed(tracker, [frame(t) for t in range(6900, 7200, 10)], auto_center=True).auto_centered


def test_gap_and_duplicates_do_not_count_as_quiet_time():
    tracker = idle_setup()
    feed(tracker, [frame(t) for t in range(1010, 3500, 10)], auto_center=True)
    assert not feed(tracker, [frame(3490)] * 400, auto_center=True).auto_centered
    assert not feed(tracker, [frame(5000)], auto_center=True).auto_centered
    assert not feed(tracker, [frame(t) for t in range(5010, 7900, 10)], auto_center=True).auto_centered
    assert feed(tracker, [frame(t) for t in range(7900, 8200, 10)], auto_center=True).auto_centered


def test_linear_jostling_does_not_count_as_idle():
    tracker = idle_setup()
    pose = feed(tracker, [frame(t, ay=1.4) for t in range(1010, 8010, 10)], auto_center=True)
    assert not pose.auto_centered


def test_auto_center_timer_handles_timestamp_wrap():
    tracker = SensorPoseTracker()
    pose = feed(tracker, [frame(t & 0xffffffff) for t in range(0xfffffe00, 0xfffffe00 + 3500, 10)],
                auto_center=True)
    assert pose.auto_centered


def test_disabling_auto_center_clears_countdown_and_keeps_pose():
    tracker = idle_setup()
    feed(tracker, [frame(t) for t in range(1010, 3500, 10)], auto_center=True)
    held = feed(tracker, [frame(t) for t in range(3500, 9000, 10)], auto_center=False)
    assert held.offset_x > 180 and not held.auto_centered
    assert not feed(tracker, [frame(t) for t in range(9000, 11800, 10)], auto_center=True).auto_centered
    assert feed(tracker, [frame(t) for t in range(11800, 12300, 10)], auto_center=True).auto_centered


def test_vertical_handle_can_auto_center_without_losing_mount_or_bias():
    from codex_whip.mount_profile import MountingProfile
    tracker = SensorPoseTracker(MountingProfile((0, 0, 1), 30, 30))
    profile = tracker.mounting
    feed(tracker, [frame(0)], auto_center=True)
    feed(tracker, [frame(t, gx=-90, ay=math.cos(math.radians(t * .09)),
                         az=math.sin(math.radians(t * .09))) for t in range(10, 1010, 10)],
         auto_center=True)
    centered = feed(tracker, [frame(t, ay=0, az=1) for t in range(1010, 4300, 10)], auto_center=True)
    assert centered.auto_centered
    assert centered.offset_x == pytest.approx(0, abs=.01)
    assert centered.offset_y == pytest.approx(0, abs=.01)
    assert tracker.mounting == profile and tracker.gyro_bias == (0, 0, 0)
    for axis in (tracker._up, tracker._right, tracker._forward):
        assert sum(v*v for v in axis) == pytest.approx(1)


def test_auto_center_rebases_frame_without_changing_attitude_or_angle_gain():
    tracker = SensorPoseTracker()
    control = SensorPoseTracker()
    def both(frames):
        batch = RawMotionBatch(1, frames[0].timestamp_ms, tuple(frames))
        a = tracker.feed_batch(batch, auto_center=True)
        control.feed_batch(batch)
        return a
    both([frame(0)])
    both([frame(t, gy=80) for t in range(10, 1010, 10)])
    centered = both([frame(t) for t in range(1010, 4310, 10)])
    assert centered.auto_centered
    assert tracker._neutral != control._neutral
    assert tracker.orientation == control.orientation
    assert (tracker._up, tracker._right, tracker._forward) == (control._up, control._right, control._forward)
    assert tracker._rotation_vector == pytest.approx((0, 0, 0), abs=1e-8)
    assert tracker._display_zero == (0, 0, 0)
    neutral = tracker._neutral
    pose = both([frame(t, gy=-10) for t in range(4310, 5310, 10)])
    assert -80 < pose.offset_x < -70  # Immediate small turn, no lost edge range.
    assert tracker._neutral == neutral
    assert tracker.orientation == control.orientation


@pytest.mark.parametrize('roll', [45, 90, 135, 180, -90])
def test_auto_center_redefines_axes_at_a_changed_grip_roll(roll):
    from codex_whip.mount_profile import MountingProfile
    tracker = SensorPoseTracker(MountingProfile((0, 0, 1), 30, 30, 2, -1))
    feed(tracker, [frame(0)], auto_center=True)
    original = (tracker._neutral, tracker._up, tracker._right, tracker._forward)
    feed(tracker, [frame(t, gz=roll, ax=math.sin(math.radians(t*roll/1000)),
                         ay=math.cos(math.radians(t*roll/1000))) for t in range(10, 1010, 10)], auto_center=True)
    ax, ay = math.sin(math.radians(roll)), math.cos(math.radians(roll))
    pose = feed(tracker, [frame(t, ax=ax, ay=ay) for t in range(1010, 4310, 10)], auto_center=True)
    assert pose.auto_centered
    assert original != (tracker._neutral, tracker._up, tracker._right, tracker._forward)
    assert tracker._up == pytest.approx((ax, ay, 0), abs=.001)
    assert tracker.mounting.yaw_sign == -1
    assert tracker.gyro_bias == (0, 0, 0)
    # Screen right is clockwise about current gravity, even after grip roll.
    pose = feed(tracker, [frame(t, gx=-ax*10, gy=-ay*10, ax=ax, ay=ay)
                          for t in range(4310, 5310, 10)], auto_center=True)
    assert pose.offset_x == pytest.approx(76, abs=2)
    assert abs(pose.offset_y) < 1
    # Recenter, then lift around the new horizontal axis with matching gravity.
    feed(tracker, [frame(t, ax=ax, ay=ay) for t in range(5310, 8710, 10)], auto_center=True)
    frames = []
    for t in range(8710, 9710, 10):
        angle = math.radians((t-8700)*.01)
        frames.append(frame(t, gx=-ay*10, gy=ax*10,
                            ax=ax*math.cos(angle), ay=ay*math.cos(angle), az=math.sin(angle)))
    pose = feed(tracker, frames, auto_center=True)
    assert pose.offset_y == pytest.approx(65, abs=2)
    assert abs(pose.offset_x) < 1


def test_manual_calibration_clears_display_offset_after_auto_center():
    tracker = idle_setup()
    feed(tracker, [frame(t) for t in range(1010, 4310, 10)], auto_center=True)
    assert tracker._display_zero == (0, 0, 0)
    tracker.calibrate_neutral(allow_motion=True)
    assert tracker._display_zero == (0, 0, 0)
