"""Dubins candidates followed by bounded forward-only pose-lattice search."""

import heapq
import math

from ..vendor.dubins import candidates
from .motion import finite, integrate, positive, valid_pose


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


def path_safe(points, obstacles, bounds, margin=8.0) -> bool:
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
        if _safe(points, circles, bounds, 8.0):
            return points, sum(lengths), "".join(modes)
    return None


def plan_path(start, goal, radius=60.0, obstacles=None, bounds=(0, 0, 4000, 4000), step=5.0, budget=3000) -> dict:
    start, goal = valid_pose(start), valid_pose(goal)
    radius, step = positive(radius, "radius"), positive(step, "step")
    if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 20000:
        raise ValueError("budget must be an integer from 1 to 20000")
    circles, bounds = environment(obstacles, bounds)
    if radius < 1 or radius > 100000 or step < 0.1 or max(bounds[2] - bounds[0], bounds[3] - bounds[1]) > 100000:
        raise ValueError("planning scale exceeds bounded implementation limits")
    step = min(step, 5.0, radius / 12)
    sample_bound = (math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1]) + 6 * math.pi * radius) / step
    if sample_bound > 50000:
        raise ValueError("planning parameters exceed the 50000-sample connector bound")
    result = {"status": "infeasible", "points": [], "length_m": 0.0, "algorithm": "dubins-six+forward-lattice-v1", "diagnostics": {"expanded": 0, "solution_quality": "none"}}
    if not _safe([start], circles, bounds, 8) or not _safe([goal], circles, bounds, 8):
        result["diagnostics"]["reason"] = "start or goal intersects the safety envelope"
        return result
    connection = _connect(start, goal, radius, step, circles, bounds)
    if connection is not None:
        points, length, family = connection
        result.update(status="succeeded", points=points, length_m=length)
        result["diagnostics"].update(family=family, solution_quality="feasible")
        return result
    # Each node is a continuous pose. Quantization only prunes duplicate states;
    # edges are exact forward arcs, never grid diagonals or in-place rotations.
    travel = radius * math.pi / 4
    cell = radius / 3
    nodes = [(start, -1, [], 0.0)]
    queue = [(math.dist(start[:2], goal[:2]), 0)]
    visited = {}
    expanded = 0
    while queue and expanded < budget:
        _, index = heapq.heappop(queue)
        pose, parent, edge, cost = nodes[index]
        key = (round(pose[0] / cell), round(pose[1] / cell), round((pose[2] % (2 * math.pi)) / (math.pi / 12)) % 24)
        if visited.get(key, math.inf) <= cost:
            continue
        visited[key] = cost
        expanded += 1
        if expanded > 1 and (expanded % 6 == 0 or math.dist(pose[:2], goal[:2]) < radius * 3):
            connection = _connect(pose, goal, radius, step, circles, bounds)
            if connection is not None:
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
        for curvature in (-1 / radius, -0.5 / radius, 0, 0.5 / radius, 1 / radius):
            count = math.ceil(travel / step)
            edge = [pose] + [integrate(pose, 1, curvature, travel * k / count) for k in range(1, count + 1)]
            if not _safe(edge, circles, bounds, 8):
                continue
            endpoint = edge[-1]
            nodes.append((endpoint, index, edge, cost + travel))
            heapq.heappush(queue, (cost + travel + 1.15 * math.dist(endpoint[:2], goal[:2]), len(nodes) - 1))
    result["status"] = "timed_out" if queue else "infeasible"
    result["diagnostics"].update(expanded=expanded, reason="expansion budget exhausted" if queue else "discretized forward search exhausted")
    return result
