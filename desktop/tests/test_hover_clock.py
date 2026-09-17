from datetime import datetime
import math
import pytest
from codex_whip.hover_clock import clock_pose, morph, shortest_angle, near_whip, ease
from codex_whip.effects import CodexWhipEffects
from codex_whip.effects import WhipPose
from codex_whip.hover_clock import cord_rotation


def test_cord_nodes_cannot_split_at_opposite_rotation_boundary():
    source = WhipPose((0,-20),(0,0),((0,0),(-40,1),(-80,-1),(-120,1)))
    target = WhipPose((0,-20),(0,0),((0,0),(40,0),(80,0),(120,0)))
    for i in range(21):
        a = i/20
        result = morph(source,target,a)
        assert result.cord[0] == result.handle_end
        for j in range(1,len(result.cord)):
            limit = math.dist(source.cord[j-1],source.cord[j])*(1-a)+math.dist(target.cord[j-1],target.cord[j])*a
            assert math.dist(result.cord[j-1],result.cord[j]) <= limit+1e-8
    mid = morph(source,target,.5)
    reverse = morph(mid,source,.01)
    assert math.dist(reverse.cord[-1],mid.cord[-1])<5


def test_rotation_branch_is_continuous_across_moving_target():
    source = WhipPose((0,-20),(0,0),((0,0),(100,0)))
    def target(angle):
        a = math.radians(angle)
        return WhipPose((0,-20),(0,0),((0,0),(100*math.cos(a),100*math.sin(a))))
    first = cord_rotation(source,target(179))
    second = cord_rotation(source,target(-179),first)
    assert math.degrees(second-first) == pytest.approx(2)
from codex_whip.hover_clock import project, project_pose, pointer_tilt


def test_projection_keeps_center_and_attached_hands():
    tilt = pointer_tilt(260,60,300,300)
    assert project((150,150),300,300,tilt) == (150,150)
    pose = clock_pose(300,300,22,datetime(2026,9,17,3))
    result = project_pose(pose,300,300,tilt)
    assert result.handle_end == result.cord[0] == (150,150)
    assert result.handle_start != pose.handle_start
    assert project((40,80),300,300,(0,0)) == (40,80)
    assert all(abs(v)<=.35 for v in pointer_tilt(10000,-10000,300,300))


def test_local_clock_hands_and_junction():
    pose = clock_pose(300,300,22,datetime(2026,9,17,3,0,0))
    assert pose.handle_end == (150,150)
    assert pose.cord[0] == (150,150)
    assert pose.handle_start[0] > 150
    assert pose.handle_start[1] == pytest.approx(150)
    assert pose.cord[-1][0] == pytest.approx(150)
    assert pose.cord[-1][1] < 150


def test_hour_includes_minutes_and_seconds():
    pose = clock_pose(300,300,22,datetime(2026,9,17,6,30,30))
    x,y = pose.handle_start
    angle = math.atan2(y-150,x-150)
    assert angle == pytest.approx((6+30.5/60)*math.tau/12-math.pi/2)


def test_shortest_rotation_wraps_midnight():
    assert math.degrees(shortest_angle(math.radians(179),math.radians(-179))) == pytest.approx(181)
    assert math.degrees(shortest_angle(math.radians(-179),math.radians(179))) == pytest.approx(-181)


def test_morph_keeps_junction_and_endpoints():
    source = CodexWhipEffects.IDLE
    target = clock_pose(300,300,len(source.cord))
    for amount in (0,.25,.5,1):
        pose = morph(source,target,amount)
        assert pose.cord[0] == pytest.approx(pose.handle_end)
    assert morph(source,target,0).handle_start == pytest.approx(source.handle_start)
    assert morph(source,target,1).cord[-1] == pytest.approx(target.cord[-1])
    assert near_whip(target,*target.handle_start)
    assert not near_whip(target,-100,-100)


def test_curve_is_monotonic_and_bounded():
    values = [ease(i/100) for i in range(101)]
    assert values == sorted(values)
    assert values[0] == pytest.approx(0,abs=1e-6)
    assert values[-1] == pytest.approx(1)
