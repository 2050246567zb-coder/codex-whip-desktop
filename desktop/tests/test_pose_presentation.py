import math

import pytest

from codex_whip.pose_presentation import PoseContinuation
from codex_whip.sensor_pose import SensorPose


def pose(x, y=0, angle=0, moving=True, centered=False):
    return SensorPose(x, y, angle, .5, moving, centered)


def test_moves_between_packets_without_mutating_measured_pose():
    track = PoseContinuation()
    track.push(pose(0), 1)
    measured = pose(12)
    track.push(measured, 1.04)
    positions = [track.sample(1.04 + i * .008).offset_x for i in range(6)]
    assert positions[0] == pytest.approx(12)
    assert all(a < b for a, b in zip(positions, positions[1:]))
    assert track.pose is measured
    assert measured.offset_x == 12


def test_gap_brakes_to_bounded_endpoint_and_holds_without_drift():
    track = PoseContinuation()
    track.push(pose(0), 1)
    track.push(pose(1000, 1000, 90), 1.08)
    endpoint = track.sample(1.16)
    assert math.hypot(endpoint.offset_x-1000, endpoint.offset_y-1000) <= 24.00001
    assert endpoint.angle_degrees <= 95
    assert track.sample(1.8) == endpoint
    assert track.sample(10) == endpoint


@pytest.mark.parametrize('interval', [0, .001, .2, .8])
def test_bursts_and_long_gaps_do_not_invent_velocity(interval):
    track = PoseContinuation()
    track.push(pose(0), 1)
    current = pose(40)
    track.push(current, 1 + interval)
    assert track.sample(1 + interval + .03) == current


def test_stop_reversal_and_calibration_discard_previous_direction():
    track = PoseContinuation()
    track.push(pose(0), 1)
    track.push(pose(12), 1.04)
    track.push(pose(6), 1.08)
    assert track.sample(1.10).offset_x < 6
    track.push(pose(5, moving=False), 1.12)
    assert track.sample(1.15).offset_x == 5
    track.push(pose(0, centered=True), 1.16)
    assert track.sample(1.18).offset_x == 0
    track.reset()
    assert track.sample(2) is None
    track.push(pose(30), 2)
    assert track.sample(2.03).offset_x == 30
