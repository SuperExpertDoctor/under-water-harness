"""Heuristic strip coverage with checked Dubins connectors and closed routes."""

import math

from .motion import positive, valid_pose
from .planning import environment, plan_path, path_safe


def plan_search(uuvs, bbox, radius=60.0, obstacles=None, bounds=(0, 0, 4000, 4000), spacing=450.0) -> dict:
    radius, spacing = positive(radius, "radius"), positive(spacing, "spacing")
    _, bbox = environment([], bbox)
    _, bounds = environment(obstacles, bounds)
    if not isinstance(uuvs, list) or any(not isinstance(uuv, dict) or not {"id", "pose"} <= uuv.keys() for uuv in uuvs):
        raise ValueError("search vehicles require id and pose")
    if any(not isinstance(uuv["id"], str) or not uuv["id"] for uuv in uuvs):
        raise ValueError("vehicle identifiers must be nonempty strings")
    for uuv in uuvs:
        valid_pose(uuv["pose"])
    if not 1 <= len(uuvs) <= 8 or len({uuv["id"] for uuv in uuvs}) != len(uuvs):
        raise ValueError("search requires one to eight uniquely identified vehicles")
    if bbox[0] < bounds[0] or bbox[1] < bounds[1] or bbox[2] > bounds[2] or bbox[3] > bounds[3]:
        raise ValueError("search bbox must be within map bounds")
    result = {"status": "infeasible", "routes": {}, "algorithm": "strip-sweep-dubins-loops-v1", "diagnostics": {"solution_quality": "none", "coverage_guarantee": False}}
    inset = 2 * radius + 16
    strip_width = (bbox[2] - bbox[0]) / len(uuvs)
    if strip_width <= 2 * inset or bbox[3] - bbox[1] <= 2 * inset:
        result["diagnostics"]["reason"] = "search strips too narrow for conservative turning clearance"
        return result
    routes = {}
    for index, uuv in enumerate(uuvs):
        start = valid_pose(uuv["pose"])
        xmin = bbox[0] + index * strip_width + inset
        xmax = bbox[0] + (index + 1) * strip_width - inset
        ymin, ymax = bbox[1] + inset, bbox[3] - inset
        lane_count = max(2, math.ceil((xmax - xmin) / spacing) + 1)
        if lane_count > 200:
            raise ValueError("search spacing creates more than 200 lanes per vehicle")
        waypoints = [start]
        for lane in range(lane_count):
            x = xmin + (xmax - xmin) * lane / (lane_count - 1)
            heading = math.pi / 2 if lane % 2 == 0 else -math.pi / 2
            # Shift a blocked sweep endpoint within its strip; connectors still
            # undergo the same Dubins and swept-obstacle validation below.
            choices = sorted(set([x]+[xmin+(xmax-xmin)*k/20 for k in range(21)]), key=lambda value: abs(value-x))
            feasible = [value for value in choices if all(path_safe([[value, y, heading]], obstacles, bounds) for y in (ymin, ymax))]
            if not feasible:
                result["diagnostics"]["reason"] = "no clear sweep endpoints within assigned strip"
                return result
            x = feasible[0]
            waypoints.extend([[x, ymin if lane % 2 == 0 else ymax, heading], [x, ymax if lane % 2 == 0 else ymin, heading]])
        waypoints.append(start)
        points = [start]
        for left, right in zip(waypoints, waypoints[1:]):
            segment = plan_path(left, right, radius=radius, obstacles=obstacles, bounds=bounds)
            if segment["status"] != "succeeded":
                result["status"] = segment["status"]
                result["diagnostics"].update(reason="coverage connector failed", uuv_id=uuv["id"], connector=segment["diagnostics"])
                return result
            points.extend(segment["points"][1:])
        routes[uuv["id"]] = points
    result.update(status="succeeded", routes=routes)
    result["diagnostics"]["solution_quality"] = "feasible"
    return result
