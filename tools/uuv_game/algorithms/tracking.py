"""Bounded geometric pursuit and moving-center distance-band orbit control."""

import math

from .motion import finite, positive, valid_pose


def tracking_control(pose, estimate, radius=60.0, speed=4.0, slot=0) -> float:
    x, y, heading = valid_pose(pose)
    radius, speed = positive(radius, "radius"), positive(speed, "speed")
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 2:
        raise ValueError("tracking slot must be zero, one or two")
    if not isinstance(estimate, dict) or not {"x", "y", "vx", "vy"} <= estimate.keys():
        raise ValueError("tracking estimate requires x, y, vx, vy")
    tx, ty, vx, vy = [finite(estimate[key], key) for key in ("x", "y", "vx", "vy")]
    dx, dy = x - tx, y - ty
    distance = math.hypot(dx, dy)
    angle = math.atan2(dy, dx) if distance > 1e-6 else heading - math.pi / 2
    desired_distance = max(180.0 + 45.0 * slot, 2.5 * radius)
    radial = max(-0.8 * speed, min(0.8 * speed, (desired_distance - distance) * 0.035))
    desired_x = vx + radial * math.cos(angle) - speed * math.sin(angle)
    desired_y = vy + radial * math.sin(angle) + speed * math.cos(angle)
    desired_heading = math.atan2(desired_y, desired_x)
    error = math.remainder(desired_heading - heading, 2 * math.pi)
    curvature = 1 / desired_distance + 1.5 * error / radius
    return max(-1 / radius, min(1 / radius, curvature))


def follow_path(pose, points, radius=60.0, lookahead=40.0) -> float:
    x, y, heading = valid_pose(pose)
    radius, lookahead = positive(radius, "radius"), positive(lookahead, "lookahead")
    if not points:
        raise ValueError("cannot follow an empty path")
    points = [valid_pose(point) for point in points]
    # Heading breaks ties where a closed route crosses itself.
    nearest = min(range(len(points)), key=lambda index: math.hypot(points[index][0] - x, points[index][1] - y) + radius * 0.2 * abs(math.remainder(points[index][2] - heading, 2 * math.pi)))
    target = points[nearest]
    travelled = 0.0
    closed = len(points) > 2 and math.dist(points[0][:2], points[-1][:2]) < 1e-6
    for offset in range(1, len(points)):
        index = nearest + offset
        if index >= len(points):
            if not closed:
                break
            index %= len(points)
        candidate = points[index]
        travelled += math.dist(target[:2], candidate[:2])
        target = candidate
        if travelled >= lookahead:
            break
    dx, dy = target[0] - x, target[1] - y
    square = dx * dx + dy * dy
    if math.cos(heading) * dx + math.sin(heading) * dy < 0:
        error = math.remainder(math.atan2(dy, dx) - heading, 2 * math.pi)
        return math.copysign(1 / radius, error)
    curvature = 0.0 if square < 1e-12 else 2 * (-math.sin(heading) * dx + math.cos(heading) * dy) / square
    return max(-1 / radius, min(1 / radius, curvature))
