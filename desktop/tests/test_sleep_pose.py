import math

import pytest

from codex_whip.hover_clock import sleep_pose


def test_sleep_pose_reads_as_continuous_z():
    pose = sleep_pose(320, 320, 80, 0)
    assert pose.handle_start[1] == pytest.approx(pose.handle_end[1])
    assert pose.handle_start[0] < pose.handle_end[0]
    assert pose.cord[0] == pytest.approx(pose.handle_end)
    assert pose.cord[-1][0] > pose.cord[-18][0]
    assert pose.cord[-1][1] == pytest.approx(pose.cord[-18][1])
    assert pose.cord[len(pose.cord) // 2][1] > pose.handle_end[1]


def test_sleep_breathing_loop_is_seamless_and_subtle():
    before = sleep_pose(320, 320, 80, 3.6 - 1e-5)
    after = sleep_pose(320, 320, 80, 3.6 + 1e-5)
    assert math.dist(before.handle_start, after.handle_start) < .01
    low = sleep_pose(320, 320, 80, 0)
    high = sleep_pose(320, 320, 80, 1.8)
    assert 10 < high.handle_start[1] - low.handle_start[1] < 25

