"""Exact forward-only planar kinematics and shared numeric validation."""

import math


def finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def positive(value, name):
    value = finite(value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def valid_pose(pose):
    if not isinstance(pose, (list, tuple)) or len(pose) != 3:
        raise ValueError("pose must contain x, y, heading")
    return [finite(value, "pose") for value in pose]


def integrate(pose: list[float], speed: float, curvature: float, dt: float) -> list[float]:
    x, y, heading = valid_pose(pose)
    speed = positive(speed, "speed")
    curvature = finite(curvature, "curvature")
    dt = finite(dt, "dt")
    if dt < 0:
        raise ValueError("dt must not be negative")
    distance = speed * dt
    angle = distance * curvature
    if not math.isfinite(distance) or not math.isfinite(angle):
        raise ValueError("motion interval overflows")
    # Midpoint/sinc form avoids cancellation for nearly straight arcs.
    scale = distance if abs(angle) < 1e-12 else distance * math.sin(angle / 2) / (angle / 2)
    result = [x + scale * math.cos(heading + angle / 2), y + scale * math.sin(heading + angle / 2), math.remainder(heading + angle, 2 * math.pi)]
    return valid_pose(result)
