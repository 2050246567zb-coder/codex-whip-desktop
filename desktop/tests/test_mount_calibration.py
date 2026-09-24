import asyncio
import json
import math
import threading
from unittest.mock import Mock

import pytest

from codex_whip.gui import GuiEventProcessor
from codex_whip.models import RawMotionBatch, RawMotionFrame, WhipEvent
from codex_whip.mount_calibration import DirectionCalibration
from codex_whip.mount_profile import MountingProfile, load_mounting_profile, save_mounting_profile
from codex_whip.sensor_pose import SensorPoseTracker, _conjugate, _multiply, _rotate, _unit
from codex_whip.settings import Settings


def quat(axis, angle):
    a = math.radians(angle) / 2
    return (math.cos(a), *(v * math.sin(a) for v in _unit(axis)))


class Motion:
    """Physical rigid-body simulation: gyro and gravity share one orientation."""
    def __init__(self, tracker, session=None, mounting=(1, 0, 0, 0), callback=None):
        self.tracker, self.session = tracker, session
        self.q = mounting
        self.t = 0
        self.callback = callback

    def feed(self, axis=(0, 1, 0), speed=0, count=60, linear=(0, 0, 0)):
        for _ in range(count):
            self.t += 10
            gyro = _rotate(_conjugate(self.q), tuple(v * speed for v in axis))
            self.q = _multiply(quat(axis, speed * .01), self.q)
            accel = _rotate(_conjugate(self.q), (linear[0], 1 + linear[1], linear[2]))
            frame = RawMotionFrame(self.t, *gyro, *accel)
            batch = RawMotionBatch(1, self.t, (frame,))
            if self.callback:
                self.callback(batch)
            else:
                self.pose = self.tracker.feed_batch(batch)
                if self.session:
                    self.session.feed(batch)
        return getattr(self, "pose", None)


def learn(mounting=(1, 0, 0, 0)):
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session, mounting)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=30, count=100)
    sim.feed()
    session.finish()
    sim.feed(speed=-30, count=100)
    sim.feed()
    session.begin()
    sim.feed(axis=(-1, 0, 0), speed=30, count=100)
    sim.feed()
    session.finish()
    return session, sim


def test_first_use_learns_up_before_right_without_changing_mount_result():
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker, first="up")
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    assert session.stage == "up_ready"
    session.begin()
    sim.feed(axis=(-1, 0, 0), speed=30, count=100)
    sim.feed()
    session.finish()
    assert session.stage == "right_ready"
    sim.feed(axis=(1, 0, 0), speed=30, count=100)
    sim.feed()
    session.begin()
    sim.feed(speed=30, count=100)
    sim.feed()
    session.finish()
    assert session.stage == "review"
    assert session.candidate.forward == pytest.approx((0, 0, 1), abs=.02)


MOUNTS = [(1, 0, 0, 0), quat((1, 0, 0), 180), quat((0, 0, 1), 90),
          quat((0, 1, 0), 180), quat((1, 2, 3), 117)]


@pytest.mark.parametrize("mounting", MOUNTS)
def test_learns_board_forward_for_arbitrary_fixed_mounting(mounting):
    session, _sim = learn(mounting)
    assert session.stage == "review"
    assert session.candidate.forward == pytest.approx(_rotate(_conjugate(mounting), (0, 0, 1)), abs=.02)
    assert session.candidate.right_angle_deg == pytest.approx(30, abs=.7)
    assert session.candidate.up_angle_deg == pytest.approx(30, abs=.7)


@pytest.mark.parametrize("mounting", MOUNTS)
@pytest.mark.parametrize("roll", [0, 65, 160])
def test_saved_mount_adapts_to_changed_grip_and_preserves_screen_directions(mounting, roll):
    session, _ = learn(mounting)
    # Different grip roll, heading and pitch, without changing PCB installation.
    new_grip = _multiply(quat((0, 1, 0), 40), _multiply(quat((1, 0, 0), 20),
                        _multiply(quat((0, 0, 1), roll), mounting)))
    tracker = SensorPoseTracker(session.candidate)
    sim = Motion(tracker, mounting=new_grip)
    sim.feed()
    assert tracker.calibrate_neutral() is not None
    pose = sim.feed(speed=10, count=100)
    assert pose.offset_x == pytest.approx(76, abs=2)
    assert abs(pose.offset_y) < 1
    sim.feed(speed=-10, count=100)
    sim.feed()
    tracker.calibrate_neutral()
    # World horizontal screen-right from gravity and the held pointing direction.
    pointing = _rotate(sim.q, session.candidate.forward)
    right = _unit((pointing[2], 0, -pointing[0]))
    pose = sim.feed(axis=tuple(-v for v in right), speed=10, count=100)
    assert pose.offset_y == pytest.approx(65, abs=2)
    assert abs(pose.offset_x) < 1


def test_missing_data_rejected_but_manual_pose_snapshot_allows_motion():
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    with pytest.raises(ValueError, match="收集"):
        session.record_neutral()
    sim = Motion(tracker, session)
    sim.feed(speed=40)
    session.record_neutral()
    assert session.stage == "right_ready"
    assert tracker.gyro_bias == (0, 0, 0)


@pytest.mark.parametrize("speed,error", [(4, "净转角"), (120, "净转角")])
def test_invalid_right_capture_never_advances(speed, error):
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=speed, count=100)
    sim.feed()
    with pytest.raises(ValueError, match=error):
        session.finish()
    assert session.stage == "right_capture"
    assert session.candidate is None
    session.retry()
    assert session.stage == "right_ready"


def test_partial_return_is_ok_and_new_step_has_its_own_start_pose():
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=45, count=100)
    sim.feed(speed=-25, count=100)
    sim.feed()
    session.finish()  # Net direction remains observable despite overshoot.
    assert session.stage == "up_ready"
    session.begin()  # No forced return to the very first pose.
    assert session.stage == "up_capture"


def test_two_similar_axes_cannot_be_saved():
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=30, count=100)
    sim.feed()
    session.finish()
    sim.feed(speed=-30, count=100)
    sim.feed()
    session.begin()
    sim.feed(speed=30, count=100)
    sim.feed()
    with pytest.raises(ValueError, match="太接近"):
        session.finish()
    assert session.candidate is None


def test_packet_loss_preserves_learning_but_requires_review_recenter():
    session, sim = learn()
    session.centered = True
    sim.t += 1000
    sim.feed(count=1)
    assert session.stage == "review"
    assert session.candidate is not None and not session.centered
    profile = MountingProfile((0, 0, 1), 30, 30)
    tracker = SensorPoseTracker(profile)
    sim = Motion(tracker)
    sim.feed()
    sim.feed(axis=(-1, 0, 0), speed=90, count=100)
    sim.feed()
    q = tracker.orientation
    with pytest.raises(ValueError, match="竖直"):
        tracker.calibrate_neutral()
    assert tracker.orientation == q
    tracker.reset()
    assert tracker.mounting == profile
    assert sim.feed(count=3).offset_x == 0  # Vertical startup doesn't crash.


def test_atomic_profile_roundtrip_and_bad_data(tmp_path, monkeypatch):
    path = tmp_path / "mounting-profile.json"
    profile = MountingProfile((0, 0, 1), 30, 30)
    assert load_mounting_profile(path) is None
    save_mounting_profile(profile, path)
    assert load_mounting_profile(path) == profile
    before = path.read_bytes()
    monkeypatch.setattr("codex_whip.mount_profile.os.replace", Mock(side_effect=OSError("disk error")))
    with pytest.raises(OSError):
        save_mounting_profile(MountingProfile((1, 0, 0), 20, 25), path)
    assert path.read_bytes() == before
    assert len(list(tmp_path.iterdir())) == 1
    for data in ["broken", "null", "[]", json.dumps({"schema_version": 8}),
                 json.dumps({"forward": [0, 0, float('nan')], "right_angle_deg": 30,
                             "up_angle_deg": 30, "schema_version": 1})]:
        path.write_text(data, encoding="utf-8")
        assert load_mounting_profile(path) is None


def test_processor_full_flow_safe_send_save_reload_and_cancel(tmp_path):
    path = tmp_path / "mounting-profile.json"
    armed = threading.Event()
    armed.set()
    events = []
    voice = Mock(pending_text="保留这段语音")
    engine = Mock()
    p = GuiEventProcessor(Settings(), armed, lambda *args: events.append(args),
                          motion_engine=engine, voice_module=voice, mounting_path=path)
    p.mount_command("open", "a")
    assert not armed.is_set()
    for source in ("device", "mouse", "motion_v3", "simulation"):
        asyncio.run(p.handle(WhipEvent(5, 600, 3, 100), source=source))
    assert all(e[0] not in ("whip", "send_result") for e in events)
    sim = Motion(p._sensor_pose, callback=lambda b: asyncio.run(p.handle(b)))
    sim.feed()
    p.mount_command("neutral", "a")
    p.mount_command("begin", "a")
    sim.feed(axis=(-1, 0, 0), speed=30, count=100)
    sim.feed()
    p.mount_command("finish", "a")
    sim.feed(axis=(1, 0, 0), speed=30, count=100)
    sim.feed()
    p.mount_command("begin", "a")
    sim.feed(speed=30, count=100)
    sim.feed()
    p.mount_command("finish", "a")
    assert p._mount_session.stage == "review"
    p.mount_command("save", "a")  # Must explicitly recenter and verify first.
    assert not path.exists()
    sim.feed(speed=-30, count=100)
    sim.feed()
    p.mount_command("center", "a")
    assert p._mount_session.centered
    p.mount_command("save", "stale-token")
    assert not path.exists()
    p.mount_command("save", "a")
    assert load_mounting_profile(path) == p._sensor_pose.mounting
    assert p._mount_session is None
    assert not armed.is_set()
    voice.feed_motion.assert_not_called()
    engine.feed_batch.assert_not_called()
    voice.clear_pending.assert_not_called()
    before = path.read_bytes()
    p.mount_command("open", "b")
    assert p._sensor_pose.mounting is None
    p.mount_command("cancel", "b")
    assert path.read_bytes() == before
    assert p._sensor_pose.mounting == load_mounting_profile(path)
    new = GuiEventProcessor(Settings(), armed, lambda *_: None, mounting_path=path)
    assert new._sensor_pose.mounting == p._sensor_pose.mounting


def test_processor_stale_stream_and_disconnect_do_not_apply(tmp_path):
    p = GuiEventProcessor(Settings(), threading.Event(), lambda *_: None,
                          mounting_path=tmp_path / "mounting-profile.json")
    p.mount_command("open", "a")
    p.mount_command("neutral", "a")
    assert p._mount_session.stage == "neutral"
    sim = Motion(p._sensor_pose, callback=lambda b: asyncio.run(p.handle(b)))
    sim.feed()
    p.mount_command("neutral", "a")
    assert p._mount_session.stage == "up_ready"
    p.mount_command("disconnect", "a")
    assert p._last_sensor_batch_at == 0
    assert p._mount_session.stage == "up_ready"
    assert not (tmp_path / "mounting-profile.json").exists()


def tremor(sim, seconds=1):
    """Sub-degree oscillations, but instantaneous speed exceeds the old gate."""
    for i in range(round(seconds * 100)):
        sim.feed(axis=(0, 1, 0), speed=12 * math.cos(2 * math.pi * 6 * i / 100), count=1)


@pytest.mark.parametrize("mounting", MOUNTS)
def test_complete_guided_capture_accepts_tremor_and_long_holds(mounting):
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session, mounting)
    tremor(sim)
    assert tracker.grip_stability().ready
    session.record_neutral()
    assert tracker.gyro_bias == (0, 0, 0)  # No tremor compensation baked in.
    session.begin()
    sim.feed(speed=30, count=100)
    tremor(sim, seconds=5)  # Waiting to click must not become a false return.
    session.finish()
    assert session.stage == "up_ready"
    sim.feed(speed=-30, count=100)
    tremor(sim)
    session.begin()
    sim.feed(axis=(-1, 0, 0), speed=30, count=100)
    tremor(sim, seconds=5)
    session.finish()
    assert session.stage == "review"
    assert session.candidate.forward == pytest.approx(_rotate(_conjugate(mounting), (0, 0, 1)), abs=.025)


def test_long_capture_has_no_timeout_in_processor():
    p = GuiEventProcessor(Settings(), threading.Event(), lambda *_: None)
    p.mount_command("open", "a")
    sim = Motion(p._sensor_pose, callback=lambda b: asyncio.run(p.handle(b)))
    sim.feed()
    p.mount_command("neutral", "a")
    p.mount_command("begin", "a")
    sim.feed(axis=(-1, 0, 0), speed=30, count=100)
    sim.feed(count=6000)  # One minute reaching for the mouse must not reset.
    assert p._mount_session.stage == "up_capture"
    assert p._sensor_pose._last_timestamp_ms is not None
    p.mount_command("finish", "a")
    assert p._mount_session.stage == "right_ready"
    assert not hasattr(p._mount_session, "_segments")  # No unbounded history.


@pytest.mark.parametrize("mounting", MOUNTS)
def test_mixed_axis_turns_with_hand_translation_learn_correct_mount(mounting):
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session, mounting)
    sim.feed()
    session.record_neutral()
    session.begin()
    # Right 30 degrees mixed with 20 degrees of elevation and >1.3g total a.
    right = (-20, 30, 0)
    sim.feed(axis=_unit(right), speed=math.sqrt(1300), count=100, linear=(.3, .5, .1))
    sim.feed()
    session.finish()
    assert session.stage == "up_ready"
    sim.feed(axis=_unit(right), speed=-math.sqrt(1300), count=100)
    sim.feed()
    session.begin()
    up = (-30, 18, 0)  # Up with a substantial, but secondary, yaw component.
    sim.feed(axis=_unit(up), speed=math.sqrt(1224), count=100, linear=(.4, .4, 0))
    sim.feed()
    session.finish()
    assert session.stage == "review"
    assert session.candidate.forward == pytest.approx(_rotate(_conjugate(mounting), (0, 0, 1)), abs=.025)


def test_secondary_axis_adjustment_is_not_main_direction_reversal():
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=30, count=100)
    sim.feed(axis=(1, 0, 0), speed=24, count=50)
    sim.feed(axis=(1, 0, 0), speed=-24, count=50)
    sim.feed()
    session.finish()
    assert session.stage == "up_ready"


def test_normal_brisk_turn_and_brief_linear_acceleration_are_not_a_whip():
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=250, count=1, linear=(0, 1.5, 0))
    sim.feed(speed=250, count=11)
    sim.feed()
    session.finish()
    assert session.stage == "up_ready"


@pytest.mark.parametrize("linear,count", [((0, 6, 0), 1), ((0, 2.5, 0), 8)])
def test_true_strong_impact_still_rejects_capture(linear, count):
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=30, count=100)
    sim.feed(linear=linear, count=count)
    sim.feed()
    with pytest.raises(ValueError, match="强冲击"):
        session.finish()
    assert session.stage == "right_capture"  # No whole-wizard reset.


def test_translation_alone_cannot_satisfy_direction_learning():
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(linear=(.3, .4, .2))
    sim.feed()
    with pytest.raises(ValueError, match="净转角"):
        session.finish()


@pytest.mark.parametrize("mounting", MOUNTS)
def test_up_capture_uses_its_own_start_board_frame(mounting):
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session, mounting)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=30, count=100)
    sim.feed()
    session.finish()
    sim.feed(speed=-22, count=100)  # Return close to, not exactly at, neutral.
    sim.feed()
    session.begin()
    actual_up_axis = _rotate(quat((0, 1, 0), 8), (-1, 0, 0))
    sim.feed(axis=actual_up_axis, speed=30, count=100)
    sim.feed()
    session.finish()
    assert session.candidate.forward == pytest.approx(_rotate(_conjugate(mounting), (0, 0, 1)), abs=.025)


@pytest.mark.parametrize("mounting", MOUNTS)
@pytest.mark.parametrize("sign", [-1, 1])
def test_right_sign_is_learned_not_prejudged_and_persists(tmp_path, mounting, sign):
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session, mounting)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=sign * 30, count=100)
    session.finish()  # Clicking during motion no longer needs a still window.
    sim.feed(speed=-sign * 30, count=100)
    sim.feed()
    session.begin()
    sim.feed(axis=(-1, 0, 0), speed=30, count=100)
    session.finish()
    assert session.candidate.yaw_sign == sign
    path = tmp_path / "mounting.json"
    save_mounting_profile(session.candidate, path)
    reloaded = load_mounting_profile(path)
    assert reloaded == session.candidate
    tracker = SensorPoseTracker(reloaded)
    follow = Motion(tracker, mounting=mounting)
    follow.feed()
    tracker.calibrate_neutral(allow_motion=True)
    pose = follow.feed(speed=sign * 10, count=100)
    assert pose.offset_x == pytest.approx(76, abs=2)


@pytest.mark.parametrize("rate", [1.2, 3, 6, 20, 90])
def test_manual_record_accepts_slow_and_normal_turn_without_endpoint_hold(rate):
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=rate, count=round(30 / rate * 100))
    session.finish()
    assert session.stage == "up_ready"
    # The 3-sample median filter intentionally delays a rate step by 1 frame.
    expected = rate * (round(30 / rate * 100) - 1) * .01
    assert session.right_angle == pytest.approx(expected, abs=.1)
    assert tracker.gyro_bias == (0, 0, 0)


def test_retryable_small_turn_and_stream_loss_preserve_completed_right():
    tracker = SensorPoseTracker()
    session = DirectionCalibration(tracker)
    sim = Motion(tracker, session)
    sim.feed()
    session.record_neutral()
    session.begin()
    sim.feed(speed=-30, count=100)
    session.finish()
    angle = session.right_angle
    session.begin()
    sim.feed(axis=(-1, 0, 0), speed=3, count=100)
    with pytest.raises(ValueError, match="采集仍保留"):
        session.finish()
    assert session.stage == "up_capture" and session.right_angle == angle
    sim.t += 500
    sim.feed(count=1)
    assert session.stage == "up_ready" and session.right_angle == angle
    sim.feed()
    session.begin()
    sim.feed(axis=(-1, 0, 0), speed=30, count=100)
    session.finish()
    assert session.candidate.yaw_sign == -1


def test_legacy_profile_retains_mapping_and_invalid_sign_rejected(tmp_path):
    path = tmp_path / "mounting.json"
    path.write_text(json.dumps({"forward": [0, 0, 1], "right_angle_deg": 30,
                               "up_angle_deg": 30, "schema_version": 1}), encoding="utf-8")
    assert load_mounting_profile(path).yaw_sign == 1
    for sign in (0, 2, True, float('nan')):
        with pytest.raises(ValueError):
            MountingProfile((0, 0, 1), 30, 30, 2, sign).validated()


def test_diagnostic_is_bounded_and_does_not_save_a_mount(tmp_path):
    p = GuiEventProcessor(Settings(), threading.Event(), lambda *_: None,
                          mounting_path=tmp_path / "mounting-profile.json")
    p.mount_command("open", "a")
    sim = Motion(p._sensor_pose, callback=lambda b: asyncio.run(p.handle(b)))
    sim.feed(count=6500)
    p.mount_command("neutral", "a")
    p.mount_command("begin", "a")
    p.mount_command("finish", "a")
    data = json.loads((tmp_path / "calibration-last-error.json").read_text(encoding="utf-8"))
    assert len(data["frames"]) == 6000
    assert data["angle_deg"] < 1 and data["action"] == "finish"
    assert not (tmp_path / "mounting-profile.json").exists()
