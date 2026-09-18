import math
import pytest
from codex_whip.hover_clock import recognizing_pose


@pytest.mark.parametrize('elapsed', [i*.05 for i in range(49)])
def test_infinity_has_visible_gap_and_connected_rope(elapsed):
    pose = recognizing_pose(320,320,80,elapsed)
    assert pose.cord[0] == pose.handle_end
    assert math.dist(pose.handle_start,pose.cord[-1]) > 20
    assert all(30 <= x <= 290 and 100 <= y <= 220 for x,y in pose.cord)
    assert max(math.dist(a,b) for a,b in zip(pose.cord,pose.cord[1:])) < 10


def test_infinity_wraps_without_a_jump():
    before = recognizing_pose(320,320,80,2.4-1e-5)
    after = recognizing_pose(320,320,80,2.4+1e-5)
    assert max(math.dist(a,b) for a,b in zip(before.cord,after.cord)) < .02
