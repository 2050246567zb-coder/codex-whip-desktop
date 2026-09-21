"""Presentation-only clock geometry; never writes sensor or overlay state."""
import math
from bisect import bisect_right
from functools import lru_cache
from datetime import datetime
from .effects import WhipPose


def shortest_angle(start, end):
    return start + (end - start + math.pi) % math.tau - math.pi


def project(point, width, height, tilt, depth=0.):
    """Rotate around the dial center in 3D, then perspective-project."""
    x,y = point[0]-width/2, point[1]-height/2
    rx,ry = tilt
    y,z = y*math.cos(rx)-depth*math.sin(rx), y*math.sin(rx)+depth*math.cos(rx)
    x,z = x*math.cos(ry)+z*math.sin(ry), -x*math.sin(ry)+z*math.cos(ry)
    camera = max(120,min(width,height))*3
    factor = camera/(camera-z)
    return width/2+x*factor, height/2+y*factor


def project_pose(pose, width, height, tilt):
    return WhipPose(project(pose.handle_start,width,height,tilt),
                    project(pose.handle_end,width,height,tilt),
                    tuple(project(p,width,height,tilt) for p in pose.cord))


def pointer_tilt(x,y,width,height):
    radius = min(width,height)*.46
    clamp = lambda v: max(-1.,min(1.,v))
    return (-clamp((y-height/2)/radius)*.35,
            clamp((x-width/2)/radius)*.35)


def ease(progress, x1=.77, x2=.175, y1=0., y2=1.):
    """Existing animation skill's (0.77, 0, 0.175, 1) movement curve."""
    x = max(0., min(1., progress))
    if x in (0., 1.):
        return x
    lo, hi = 0., 1.
    for _ in range(24):
        t = (lo + hi) / 2
        value = 3 * (1-t)**2 * t * x1 + 3 * (1-t) * t*t * x2 + t**3
        if value < x:
            lo = t
        else:
            hi = t
    return 3*(1-t)**2*t*y1 + 3*(1-t)*t*t*y2 + t**3


def clock_pose(width, height, nodes, now=None):
    now = now or datetime.now()
    minutes = now.minute + (now.second + now.microsecond / 1e6) / 60
    hour = ((now.hour % 12) + minutes / 60) * math.tau / 12 - math.pi/2
    minute = minutes * math.tau / 60 - math.pi/2
    pivot = width/2, height/2
    radius = min(width, height) * .38
    def point(angle, length):
        return pivot[0]+math.cos(angle)*length, pivot[1]+math.sin(angle)*length
    return WhipPose(point(hour, radius*.57), pivot,
                    tuple(point(minute, radius*.85*i/max(1,nodes-1)) for i in range(nodes)))


def loading_pose(width, height, nodes, elapsed):
    # Continuous open arc: handle forms its leading segment, cord the remainder.
    angle = elapsed*math.tau/2.4
    radius = min(width,height)*.25
    def point(t):
        a = angle + t
        return width/2+math.cos(a)*radius, height/2+math.sin(a)*radius
    return WhipPose(point(0),point(.38),
        tuple(point(.38+i/max(1,nodes-1)*(math.tau-.9-.38)) for i in range(nodes)))


@lru_cache(maxsize=8)
def _infinity_track(width, height):
    """Arc-length lookup avoids accelerating through the figure-eight waist."""
    size = min(width, height)
    points = tuple((width/2 + size*.36*math.sin(i*math.tau/512),
                    height/2 + size*.17*math.sin(i*math.tau/256))
                   for i in range(513))
    lengths = [0.]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    return points, tuple(lengths)


def recognizing_pose(width, height, nodes, elapsed):
    """Open infinity: straight grip leads the rope, with a travelling end gap."""
    points, lengths = _infinity_track(width, height)
    phase = elapsed / 2.4
    def point(fraction):
        distance = ((phase + fraction) % 1.) * lengths[-1]
        index = min(511, bisect_right(lengths, distance)-1)
        amount = (distance-lengths[index])/(lengths[index+1]-lengths[index])
        a, b = points[index:index+2]
        return tuple(x+(y-x)*amount for x,y in zip(a,b))
    # Never close the tip onto the grip butt: 8% of the track stays empty.
    return WhipPose(point(0), point(.065),
                    tuple(point(.065 + i/max(1,nodes-1)*.855) for i in range(nodes)))


def sleep_pose(width, height, nodes, elapsed):
    """Continuous Z silhouette with a slow, breath-like vertical float."""
    size = min(width, height)
    center_x = width / 2
    center_y = height / 2
    # The cosine loop has zero velocity at both reversals, like calm breathing.
    breath = (1 - math.cos((elapsed % 3.6) / 3.6 * math.tau)) / 2
    offset_y = (breath - .5) * size * .055
    scale = 1 + breath * .018

    def point(x, y):
        return center_x + x * size * scale, center_y + y * size * scale + offset_y

    start = point(-.25, -.18)
    joint = point(.23, -.18)

    def cord_point(fraction):
        # Most nodes form the diagonal; the final third forms the lower bar.
        if fraction <= .67:
            amount = fraction / .67
            return point(.23 - .46 * amount, -.18 + .36 * amount)
        amount = (fraction - .67) / .33
        return point(-.23 + .46 * amount, .18)

    return WhipPose(start, joint, tuple(
        cord_point(index / max(1, nodes - 1)) for index in range(nodes)
    ))


def cord_rotation(source, target, previous=None):
    def direction(pose):
        x,y = pose.handle_end
        point = max(pose.cord, key=lambda p:(p[0]-x)**2+(p[1]-y)**2)
        return math.atan2(point[1]-y,point[0]-x)
    delta = direction(target)-direction(source)
    return shortest_angle(0. if previous is None else previous,delta)


def morph(source, target, amount, cord_turn=None):
    """Rotate the whole cord coherently, blending its shape in that frame.

    Segment lengths remain bounded by the weighted source/target lengths;
    neighboring nodes can never take opposite angular branches.
    """
    a = max(0., min(1., amount))
    if a == 0:
        return source
    if a == 1:
        return target
    pivot = tuple(s+(t-s)*a for s,t in zip(source.handle_end,target.handle_end))
    def point(s,t):
        sx,sy = s[0]-source.handle_end[0], s[1]-source.handle_end[1]
        tx,ty = t[0]-target.handle_end[0], t[1]-target.handle_end[1]
        start = math.atan2(sy,sx)
        end = shortest_angle(start, math.atan2(ty,tx))
        angle = start+(end-start)*a
        radius = math.hypot(sx,sy)*(1-a)+math.hypot(tx,ty)*a
        return pivot[0]+math.cos(angle)*radius, pivot[1]+math.sin(angle)*radius
    turn = cord_rotation(source,target) if cord_turn is None else cord_turn
    def rotate(x,y,angle):
        c,s = math.cos(angle),math.sin(angle)
        return x*c-y*s,x*s+y*c
    cord = []
    for s,t in zip(source.cord,target.cord):
        sx,sy = rotate(s[0]-source.handle_end[0],s[1]-source.handle_end[1],turn*a)
        tx,ty = rotate(t[0]-target.handle_end[0],t[1]-target.handle_end[1],-turn*(1-a))
        cord.append((pivot[0]+sx*(1-a)+tx*a,pivot[1]+sy*(1-a)+ty*a))
    return WhipPose(point(source.handle_start,target.handle_start), pivot,tuple(cord))


def near_whip(pose, x, y, tolerance=14):
    points = (pose.handle_start, pose.handle_end, *pose.cord)
    for start,end in zip(points,points[1:]):
        dx,dy = end[0]-start[0], end[1]-start[1]
        length = dx*dx+dy*dy
        t = max(0.,min(1.,((x-start[0])*dx+(y-start[1])*dy)/length)) if length else 0
        if math.hypot(x-start[0]-t*dx,y-start[1]-t*dy) <= tolerance:
            return True
    return False
