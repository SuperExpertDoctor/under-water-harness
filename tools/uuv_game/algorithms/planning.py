"""Dubins candidates followed by bounded forward-only pose-lattice search."""

import heapq
import math

from ..config import Config, algorithm_settings
from ..vendor.dubins import candidates
from .motion import finite, integrate, positive, valid_pose


_PLANNING = algorithm_settings("planning")


def environment(obstacles, bounds):
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise ValueError("bounds must contain xmin, ymin, xmax, ymax")
    bounds = [finite(value, "bounds") for value in bounds]
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError("bounds must have positive area")
    circles = []
    for obstacle in obstacles or []:
        if not isinstance(obstacle, dict) or not {"x", "y", "radius"} <= obstacle.keys():
            raise ValueError("obstacles must be circles with x, y, radius")
        radius = finite(obstacle["radius"], "obstacle radius")
        if radius < 0:
            raise ValueError("obstacle radius must not be negative")
        circles.append((finite(obstacle["x"], "obstacle x"), finite(obstacle["y"], "obstacle y"), radius))
    return circles, bounds


def _safe(points, circles, bounds, margin):
    if not points:
        return False
    for point in points:
        if not bounds[0] + margin <= point[0] <= bounds[2] - margin or not bounds[1] + margin <= point[1] <= bounds[3] - margin:
            return False
        if any(math.hypot(point[0] - x, point[1] - y) <= radius + margin for x, y, radius in circles):
            return False
    for left, right in zip(points, points[1:]):
        dx, dy = right[0] - left[0], right[1] - left[1]
        square = dx * dx + dy * dy
        angle = abs(math.remainder(right[2] - left[2], 2 * math.pi))
        # Dense constant-curvature samples are required; do not certify sparse
        # half/full-turn arcs whose chord hides their swept region.
        if angle > math.pi / 2 + 1e-9:
            return False
        sagitta = math.sqrt(square) * math.tan(angle / 4) / 2
        padding = margin + sagitta
        if min(left[0], right[0]) < bounds[0] + padding or max(left[0], right[0]) > bounds[2] - padding or min(left[1], right[1]) < bounds[1] + padding or max(left[1], right[1]) > bounds[3] - padding:
            return False
        for x, y, radius in circles:
            fraction = 0 if square == 0 else max(0, min(1, ((x - left[0]) * dx + (y - left[1]) * dy) / square))
            if math.hypot(left[0] + fraction * dx - x, left[1] + fraction * dy - y) <= radius + padding:
                return False
    return True


def path_safe(points, obstacles, bounds, margin=_PLANNING["safety_margin_m"]) -> bool:
    """Conservative swept-circle check for dense piecewise constant-curvature paths.

    This is a geometry check, not a certificate for arbitrary undersampled curves
    or inter-vehicle separation. The planner samples every primitive separately.
    """
    if not isinstance(points, (list, tuple)):
        raise ValueError("points must be a list of poses")
    circles, bounds = environment(obstacles, bounds)
    margin = finite(margin, "margin")
    if margin < 0:
        raise ValueError("margin must not be negative")
    return _safe([valid_pose(point) for point in points], circles, bounds, margin)


def _sample(start, modes, lengths, radius, step):
    points = [start[:]]
    for mode, length in zip(modes, lengths):
        if length < 1e-9:
            continue
        curvature = {"L": 1 / radius, "R": -1 / radius, "S": 0}[mode]
        count = math.ceil(length / step)
        origin = points[-1]
        points.extend(integrate(origin, 1, curvature, length * index / count) for index in range(1, count + 1))
    return points


def _connect(start, goal, radius, step, circles, bounds):
    for modes, lengths in candidates(start, goal, radius):
        points = _sample(start, modes, lengths, radius, step)
        if _safe(points, circles, bounds, _PLANNING["safety_margin_m"]):
            return points, sum(lengths), "".join(modes)
    return None


def _respects_time(points, start_length, constraints, speed):
    if not constraints:
        return True
    travelled = start_length
    previous = points[0]
    for point in points:
        travelled += math.dist(previous[:2], point[:2])
        at = travelled / speed
        if any(t0 <= at <= t1 and math.dist(point[:2], (x, y)) < radius
               for x, y, t0, t1, radius in constraints):
            return False
        previous = point
    return True


def plan_path(start, goal, radius=_PLANNING["radius_m"], obstacles=None, bounds=(0, 0, Config().width, Config().height),
              step=_PLANNING["step_m"], budget=_PLANNING["budget"], temporal_constraints=(),
              speed=_PLANNING["speed_mps"], start_time_s=0.0) -> dict:
    start, goal = valid_pose(start), valid_pose(goal)
    radius, step, speed = positive(radius, "radius"), positive(step, "step"), positive(speed, "speed")
    start_time_s = finite(start_time_s, "start time")
    if start_time_s < 0:
        raise ValueError("start time must not be negative")
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= _PLANNING["maximum_expansion_budget"]:
        raise ValueError(f"budget must be an integer from 1 to {_PLANNING['maximum_expansion_budget']}")
    circles, bounds = environment(obstacles, bounds)
    constraints = []
    for constraint in temporal_constraints:
        if len(constraint) != 5:
            raise ValueError("temporal constraint requires x, y, t0, t1, radius")
        x, y, t0, t1, clearance = [finite(value, "temporal constraint") for value in constraint]
        if t0 > t1 or clearance <= 0:
            raise ValueError("invalid temporal constraint interval or radius")
        constraints.append((x, y, t0, t1, clearance))
    if radius < _PLANNING["minimum_radius_m"] or radius > _PLANNING["maximum_map_span_m"] or step < _PLANNING["minimum_step_m"] or max(bounds[2] - bounds[0], bounds[3] - bounds[1]) > _PLANNING["maximum_map_span_m"]:
        raise ValueError("planning scale exceeds bounded implementation limits")
    step = min(step, _PLANNING["step_m"], radius / (_PLANNING["heading_bins"] / 2))
    sample_bound = (math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]) + 6 * math.pi * radius) / step
    if sample_bound > _PLANNING["connector_sample_limit"]:
        raise ValueError(f"planning parameters exceed the {_PLANNING['connector_sample_limit']}-sample connector bound")
    result = {"status": "infeasible", "points": [], "length_m": 0.0, "algorithm": "forward-hybrid-a-star-dubins-v1", "diagnostics": {"expanded": 0, "solution_quality": "none"}}
    if not _safe([start], circles, bounds, _PLANNING["safety_margin_m"]) or not _safe([goal], circles, bounds, _PLANNING["safety_margin_m"]):
        result["diagnostics"]["reason"] = "start or goal intersects the safety envelope"
        return result
    connection = _connect(start, goal, radius, step, circles, bounds)
    if connection is not None and _respects_time(connection[0], speed*start_time_s, constraints, speed):
        points, length, family = connection
        result.update(status="succeeded", points=points, length_m=length)
        result["diagnostics"].update(family=family, solution_quality="feasible")
        return result
    # Each node is a continuous pose. Quantization only prunes duplicate states;
    # edges are exact forward arcs, never grid diagonals or in-place rotations.
    travel = radius * math.pi / _PLANNING["lattice_turn_divisor"]
    cell = radius / _PLANNING["lattice_cell_divisor"]
    nodes = [(start, -1, [], 0.0)]
    queue = [(math.dist(start[:2], goal[:2]), 0)]
    visited = {}
    expanded = 0
    while queue and expanded < budget:
        _, index = heapq.heappop(queue)
        pose, parent, edge, cost = nodes[index]
        key = (round(pose[0] / cell), round(pose[1] / cell), round((pose[2] % (2 * math.pi)) / (2 * math.pi / _PLANNING["heading_bins"])) % _PLANNING["heading_bins"],
               round(cost / (speed * _PLANNING["constraint_time_bin_s"])) if constraints else 0)
        if visited.get(key, math.inf) <= cost:
            continue
        visited[key] = cost
        expanded += 1
        if expanded > 1 and (expanded % _PLANNING["connector_attempt_interval"] == 0 or math.dist(pose[:2], goal[:2]) < radius * _PLANNING["connector_near_radius_factor"]):
            connection = _connect(pose, goal, radius, step, circles, bounds)
            if connection is not None and _respects_time(connection[0], cost+speed*start_time_s, constraints, speed):
                tail, length, family = connection
                edges = [tail[1:]]
                cursor = index
                while nodes[cursor][1] != -1:
                    edges.append(nodes[cursor][2][1:])
                    cursor = nodes[cursor][1]
                points = [start[:]]
                for fragment in reversed(edges):
                    points.extend(fragment)
                result.update(status="succeeded", points=points, length_m=cost + length)
                result["diagnostics"].update(expanded=expanded, family=family, solution_quality="feasible")
                return result
        for curvature_fraction in _PLANNING["curvature_fractions"]:
            curvature = curvature_fraction / radius
            count = math.ceil(travel / step)
            edge = [pose] + [integrate(pose, 1, curvature, travel * k / count) for k in range(1, count + 1)]
            if not _safe(edge, circles, bounds, _PLANNING["safety_margin_m"]) or not _respects_time(edge, cost+speed*start_time_s, constraints, speed):
                continue
            endpoint = edge[-1]
            nodes.append((endpoint, index, edge, cost + travel))
            heapq.heappush(queue, (cost + travel + math.dist(endpoint[:2], goal[:2]), len(nodes) - 1))
    result["status"] = "timed_out" if queue else "infeasible"
    result["diagnostics"].update(expanded=expanded, reason="expansion budget exhausted" if queue else "discretized forward search exhausted")
    return result
