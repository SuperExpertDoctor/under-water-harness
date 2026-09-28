"""Heuristic strip coverage with checked Dubins connectors and closed routes."""

import math

from ..config import Config, algorithm_settings
from .conflicts import resolve_conflicts
from .motion import positive, valid_pose
from .planning import environment, plan_path, path_safe

_COVERAGE = algorithm_settings("coverage")

def plan_search(uuvs, bbox, radius=Config().radius, obstacles=None,
                bounds=(0, 0, Config().width, Config().height), spacing=_COVERAGE["lane_spacing_m"]) -> dict:
    radius, spacing = positive(radius, "radius"), positive(spacing, "spacing")
    _, bbox = environment([], bbox)
    _, bounds = environment(obstacles, bounds)
    if not isinstance(uuvs, list) or any(not isinstance(uuv, dict) or not {"id", "pose"} <= uuv.keys() for uuv in uuvs):
        raise ValueError("search vehicles require id and pose")
    if any(not isinstance(uuv["id"], str) or not uuv["id"] for uuv in uuvs):
        raise ValueError("vehicle identifiers must be nonempty strings")
    for uuv in uuvs:
        valid_pose(uuv["pose"])
    if not 1 <= len(uuvs) <= Config().fleet_size or len({uuv["id"] for uuv in uuvs}) != len(uuvs):
        raise ValueError("search requires one to eight uniquely identified vehicles")
    if bbox[0] < bounds[0] or bbox[1] < bounds[1] or bbox[2] > bounds[2] or bbox[3] > bounds[3]:
        raise ValueError("search bbox must be within map bounds")
    result = {"status": "infeasible", "routes": {}, "algorithm": "strip-sweep-dubins-loops-v1", "diagnostics": {"solution_quality": "none", "coverage_guarantee": False}}
    inset = 2 * radius + _COVERAGE["strip_inset_extra_m"]
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
        if lane_count > _COVERAGE["maximum_lanes"]:
            raise ValueError("search spacing creates more than 200 lanes per vehicle")
        waypoints = [start]
        for lane in range(lane_count):
            x = xmin + (xmax - xmin) * lane / (lane_count - 1)
            heading = math.pi / 2 if lane % 2 == 0 else -math.pi / 2
            # Shift a blocked sweep endpoint within its strip; connectors still
            # undergo the same Dubins and swept-obstacle validation below.
            samples = _COVERAGE["sweep_endpoint_samples"]
            choices = sorted(set([x]+[xmin+(xmax-xmin)*k/samples for k in range(samples+1)]), key=lambda value: abs(value-x))
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
    joint = resolve_conflicts({uuv["id"]: uuv["pose"] for uuv in uuvs},
        {name: path[-1] for name, path in routes.items()}, routes, obstacles,
        cycle_start_indices={name: 0 for name in routes})
    result["diagnostics"]["conflict_solver"] = joint["diagnostics"]["algorithm"]
    if joint["status"] != "succeeded":
        result["diagnostics"].update(reason="joint_route_conflict", conflict=joint["diagnostics"])
        return result
    result.update(status="succeeded", routes=joint["routes"], cycle_start_indices=joint["cycle_start_indices"])
    result["diagnostics"]["solution_quality"] = "feasible"
    return result


def plan_region_search(uuv, region, obstacles):
    """Clip scan lanes to owned cells, permitting turns elsewhere in the map.

    Routes are candidates, not coverage certificates: sensor observations alone
    update the ledger. The 560 m swath includes a 20 percent overlap allowance.
    """
    start = valid_pose(uuv["pose"])
    cells = set(map(tuple, region["cells"]))
    if not cells or any(len(c) != 2 or any(isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < Config().width/Config().cell for v in c) for c in cells):
        raise ValueError("region requires nonempty valid grid cells")
    pending = set(map(tuple, region.get("scan_cells", region["cells"])))
    if not pending or not pending <= cells:
        raise ValueError("scan cells must be a nonempty subset of responsibility cells")
    cells = pending
    result = {"status": "infeasible", "routes": {}, "diagnostics": {
        "coverage_guarantee": False, "sensor_range_m": Config().sensor_range, "lane_spacing_m": _COVERAGE["lane_spacing_m"],
        "connector_budget": _COVERAGE["connector_budget"], "reason": "no feasible bounded sweep"}}
    widths = [max(c[axis] for c in cells)-min(c[axis] for c in cells) for axis in (0, 1)]
    for axis in sorted((0, 1), key=lambda a: (widths[a], a)):
        low, high = min(c[axis] for c in cells), max(c[axis] for c in cells)
        count = max(1, math.ceil((high-low+1)/_COVERAGE["sweep_stride_cells"]))
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
                cell, world = Config().cell, Config().height
                positions = [[(lane+.5)*cell, world-(value+.5)*cell] if axis == 0 else [(value+.5)*cell, world-(lane+.5)*cell] for value in endpoints]
                inset = _COVERAGE["patrol_inset_m"]
                positions = [[max(inset, min(Config().width-inset, x)), max(inset, min(Config().height-inset, y))] for x, y in positions]
                heading = math.atan2(positions[1][1]-positions[0][1], positions[1][0]-positions[0][0])
                waypoints.extend([x, y, heading] for x, y in positions)
        if all(math.dist(point[:2], waypoints[0][:2]) < 1 for point in waypoints):
            x, y, heading = waypoints[0]
            offset = _COVERAGE["single_cell_turn_offset_m"]
            options = [[x-sign*offset*math.sin(heading), y+sign*offset*math.cos(heading), math.remainder(heading+math.pi, 2*math.pi)] for sign in (1, -1)]
            opposite = next((point for point in options if path_safe([point], obstacles, [0, 0, Config().width, Config().height])), None)
            if opposite is None:
                continue
            waypoints = [waypoints[0], opposite]
        waypoints = waypoints+[waypoints[0]]
        points, length = [waypoints[0]], 0.0
        waypoint_indices = []
        for left, right in zip(waypoints, waypoints[1:]):
            segment = plan_path(left, right, obstacles=obstacles, budget=_COVERAGE["connector_budget"])
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
                +_COVERAGE["entry_heading_penalty_m"]*abs(math.remainder(patrol[i][2]-start[2], 2*math.pi)), i))[:_COVERAGE["entry_candidates"]]
            entries = []
            for index in candidates:
                segment = plan_path(start, patrol[index], obstacles=obstacles, budget=_COVERAGE["connector_budget"])
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
            reserve = min(start[0], start[1], Config().width-start[0], Config().height-start[1])+_COVERAGE["exit_reserve_m"]
            if length+reserve > uuv.get("remaining_range_m", math.inf):
                cumulative = [0.0]
                for left, right in zip(points, points[1:]):
                    cumulative.append(cumulative[-1]+math.dist(left[:2], right[:2]))
                available = uuv["remaining_range_m"]-reserve-cumulative[cycle_start]
                for fraction in _COVERAGE["partial_patrol_fractions"]:
                    cutoff = next((i for i in range(cycle_start+1, len(points)) if cumulative[i]-cumulative[cycle_start] >= available*fraction), len(points)-1)
                    if cumulative[cutoff]-cumulative[cycle_start] < _COVERAGE["minimum_partial_sweep_m"]:
                        continue
                    closure = plan_path(points[cutoff], points[cycle_start], obstacles=obstacles, budget=_COVERAGE["connector_budget"])
                    partial_length = cumulative[cutoff]+closure["length_m"]+_COVERAGE["partial_length_padding_m"]
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
            math.dist(start[:2], [(cell[0]+.5)*Config().cell, Config().height-(cell[1]+.5)*Config().cell]), cell))
        local = plan_region_search({**uuv, "allow_partial_patrol": False},
                                   {**region, "scan_cells": [nearest]}, obstacles)
        if local["status"] == "succeeded":
            local.update(requires_energy_rotation=True, requires_replan_after_cycle=True)
            local["diagnostics"].update(partial_patrol=True, local_revisit=True,
                deferred_scan_cells=len(pending-{tuple(nearest)}),
                deferred_reason=result["diagnostics"].get("reason"), coverage_guarantee=False)
            return local
    return result
