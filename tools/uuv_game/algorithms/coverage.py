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


def plan_region_search(uuv, region, obstacles):
    """Clip scan lanes to owned cells, permitting turns elsewhere in the map.

    Routes are candidates, not coverage certificates: sensor observations alone
    update the ledger. The 560 m swath includes a 20 percent overlap allowance.
    """
    start = valid_pose(uuv["pose"])
    cells = set(map(tuple, region["cells"]))
    if not cells or any(len(c) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < 40 for v in c) for c in cells):
        raise ValueError("region requires nonempty valid grid cells")
    pending = set(map(tuple, region.get("scan_cells", region["cells"])))
    if not pending or not pending <= cells:
        raise ValueError("scan cells must be a nonempty subset of responsibility cells")
    cells = pending
    result = {"status": "infeasible", "routes": {}, "diagnostics": {
        "coverage_guarantee": False, "sensor_range_m": 350, "lane_spacing_m": 500,
        "connector_budget": 60, "reason": "no feasible bounded sweep"}}
    widths = [max(c[axis] for c in cells)-min(c[axis] for c in cells) for axis in (0, 1)]
    for axis in sorted((0, 1), key=lambda a: (widths[a], a)):
        low, high = min(c[axis] for c in cells), max(c[axis] for c in cells)
        count = max(1, math.ceil((high-low+1)/5))
        available = sorted({cell[axis] for cell in cells})
        lanes = sorted({min(available, key=lambda value: (abs(value-(low+(high-low)*(i+0.5)/count)), value)) for i in range(count)})
        waypoints = []
        for index, lane in enumerate(lanes):
            cross = sorted(c[1-axis] for c in cells if c[axis] == lane)
            if not cross:
                continue
            runs = [[cross[0]]]
            for value in cross[1:]:
                if value != runs[-1][-1]+1:
                    runs.append([])
                runs[-1].append(value)
            if index % 2:
                runs.reverse()
            for run in runs:
                endpoints = [run[0], run[-1]] if index % 2 == 0 else [run[-1], run[0]]
                positions = [[lane*100+50, 3950-value*100] if axis == 0 else [value*100+50, 3950-lane*100] for value in endpoints]
                positions = [[max(200, min(3800, x)), max(200, min(3800, y))] for x, y in positions]
                heading = math.atan2(positions[1][1]-positions[0][1], positions[1][0]-positions[0][0])
                waypoints.extend([x, y, heading] for x, y in positions)
        if all(math.dist(point[:2], waypoints[0][:2]) < 1 for point in waypoints):
            x, y, heading = waypoints[0]
            options = [[x-sign*120*math.sin(heading), y+sign*120*math.cos(heading), math.remainder(heading+math.pi, 2*math.pi)] for sign in (1, -1)]
            opposite = next((point for point in options if path_safe([point], obstacles, [0, 0, 4000, 4000])), None)
            if opposite is None:
                continue
            waypoints = [waypoints[0], opposite]
        waypoints = waypoints+[waypoints[0]]
        points, length = [waypoints[0]], 0.0
        waypoint_indices = []
        for left, right in zip(waypoints, waypoints[1:]):
            segment = plan_path(left, right, obstacles=obstacles, budget=60)
            if segment["status"] != "succeeded":
                result["diagnostics"]["connector"] = segment["diagnostics"]
                break
            points.extend(segment["points"][1:])
            length += segment["length_m"]
            waypoint_indices.append(len(points)-1)
        else:
            # Enter the closed patrol at a sampled pose, including its tangent.
            # Nearby lane middles must not require visiting a distant endpoint.
            patrol = points[:-1]
            candidates = sorted(range(len(patrol)), key=lambda i: (
                math.dist(start[:2], patrol[i][:2])
                +60*abs(math.remainder(patrol[i][2]-start[2], 2*math.pi)), i))[:6]
            entries = []
            for index in candidates:
                segment = plan_path(start, patrol[index], obstacles=obstacles, budget=60)
                if segment["status"] == "succeeded":
                    entries.append((segment["length_m"], index, segment))
            if not entries:
                result["diagnostics"]["reason"] = "no feasible patrol entry"
                continue
            entry_length, entry, segment = min(entries, key=lambda item: (item[0], item[1]))
            cycle_start = len(segment["points"])-1
            points = segment["points"]+patrol[entry+1:]+patrol[:entry+1]
            waypoint_indices = [cycle_start+(index-entry) % len(patrol) for index in waypoint_indices]
            length += entry_length
            reserve = min(start[0], start[1], 4000-start[0], 4000-start[1])+600
            if length+reserve > uuv.get("remaining_range_m", math.inf):
                cumulative = [0.0]
                for left, right in zip(points, points[1:]):
                    cumulative.append(cumulative[-1]+math.dist(left[:2], right[:2]))
                available = uuv["remaining_range_m"]-reserve-cumulative[cycle_start]
                for fraction in (0.5, 0.35, 0.2):
                    cutoff = next((i for i in range(cycle_start+1, len(points)) if cumulative[i]-cumulative[cycle_start] >= available*fraction), len(points)-1)
                    if cumulative[cutoff]-cumulative[cycle_start] < 120:
                        continue
                    closure = plan_path(points[cutoff], points[cycle_start], obstacles=obstacles, budget=60)
                    partial_length = cumulative[cutoff]+closure["length_m"]+1
                    if closure["status"] != "succeeded" or partial_length+reserve > uuv["remaining_range_m"]:
                        continue
                    result["diagnostics"].update(partial_patrol=True,
                        deferred_waypoints=sum(index > cutoff for index in waypoint_indices),
                        deferred_path_m=max(0, length-cumulative[cutoff]))
                    points = points[:cutoff+1]+closure["points"][1:]
                    length = partial_length
                    result.update(requires_energy_rotation=True, requires_replan_after_cycle=True)
                    break
                else:
                    result["diagnostics"]["reason"] = "no energy-feasible patrol prefix including exit reserve"
                    continue
            result.update(status="succeeded", routes={uuv["id"]: points}, length_m=length,
                          cycle_start_indices={uuv["id"]: cycle_start})
            result["diagnostics"].pop("reason", None)
            result["diagnostics"].update(sweep_axis=axis, solution_quality="feasible")
            return result
    if uuv.get("allow_partial_patrol") is True:
        nearest = min(region["cells"], key=lambda cell: (
            math.dist(start[:2], [cell[0]*100+50, 3950-cell[1]*100]), cell))
        local = plan_region_search({**uuv, "allow_partial_patrol": False},
                                   {**region, "scan_cells": [nearest]}, obstacles)
        if local["status"] == "succeeded":
            local.update(requires_energy_rotation=True, requires_replan_after_cycle=True)
            local["diagnostics"].update(partial_patrol=True, local_revisit=True,
                deferred_scan_cells=len(pending-{tuple(nearest)}),
                deferred_reason=result["diagnostics"].get("reason"), coverage_guarantee=False)
            return local
    return result
