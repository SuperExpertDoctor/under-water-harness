"""Bounded CBS over forward-only pose routes, with continuous swept separation."""

import bisect
import heapq
import math

from ..config import Config, algorithm_settings
from .planning import plan_path

_CBS = algorithm_settings("cbs")

def _schedule(points, speed):
    distance = [0.0]
    for left, right in zip(points, points[1:]):
        delta = abs(math.remainder(right[2]-left[2], math.tau))
        factor = delta/(2*math.sin(delta/2)) if delta > 1e-9 else 1.0
        distance.append(distance[-1]+math.dist(left[:2], right[:2])*factor)
    return [value/speed for value in distance]


def _position(points, schedule, at):
    index = max(0, min(len(schedule)-2, bisect.bisect_right(schedule, at)-1))
    if at >= schedule[-1]:
        return points[-1][:2]
    fraction = (at-schedule[index])/max(1e-12, schedule[index+1]-schedule[index])
    return [points[index][axis]+fraction*(points[index+1][axis]-points[index][axis]) for axis in (0, 1)]


def first_conflict(routes, separation=_CBS["separation_m"], speed=_CBS["speed_mps"], horizon_s=_CBS["horizon_s"]):
    """Earliest swept disk conflict; intermediate checks include segment crossing."""
    names = sorted(routes)
    schedules = {name: _schedule(routes[name], speed) for name in names}
    maximum = min(horizon_s, max((times[-1] for times in schedules.values()), default=0))
    sampling = _CBS["sample_s"]
    for index in range(math.ceil(maximum/sampling)):
        t0, t1 = index*sampling, min(maximum, (index+1)*sampling)
        for i, first in enumerate(names):
            a = _position(routes[first], schedules[first], t0)
            b = _position(routes[first], schedules[first], t1)
            for second in names[i+1:]:
                c = _position(routes[second], schedules[second], t0)
                d = _position(routes[second], schedules[second], t1)
                rx, ry = a[0]-c[0], a[1]-c[1]
                vx, vy = b[0]-d[0]-rx, b[1]-d[1]-ry
                scale = vx*vx+vy*vy
                fraction = max(0, min(1, -(rx*vx+ry*vy)/scale)) if scale else 0.0
                if math.hypot(rx+vx*fraction, ry+vy*fraction) < separation:
                    at = t0+(t1-t0)*fraction
                    p = _position(routes[first], schedules[first], at)
                    q = _position(routes[second], schedules[second], at)
                    return {"members": (first, second), "time_s": at,
                            "position": [(p[0]+q[0])/2, (p[1]+q[1])/2]}
    return None


def resolve_conflicts(starts, goals, routes, obstacles, *, budget=_CBS["budget"], horizon_s=_CBS["horizon_s"],
                      separation=_CBS["separation_m"], speed=_CBS["speed_mps"],
                      low_level_budget=_CBS["low_level_budget"], bounds=(0, 0, Config().width, Config().height),
                      cycle_start_indices=None):
    """CBS conflict tree; no unsafe partial result is exposed on budget exhaustion."""
    result = {"status": "infeasible", "routes": {}, "diagnostics": {"algorithm": "bounded-pose-cbs-v1", "expanded": 0}}
    if not routes or set(starts) != set(goals) or set(starts) != set(routes) or budget < 0:
        result["diagnostics"]["reason"] = "invalid_joint_request"
        return result
    cycles = cycle_start_indices or {}
    queue = [(sum(len(route) for route in routes.values()), 0, routes,
              {member: () for member in routes}, dict(cycles))]
    sequence = 0
    while queue and result["diagnostics"]["expanded"] < budget:
        _, _, current, constraints, current_cycles = heapq.heappop(queue)
        result["diagnostics"]["expanded"] += 1
        conflict = first_conflict(current, separation=separation, speed=speed, horizon_s=horizon_s)
        if conflict is None:
            result.update(status="succeeded", routes=current, cycle_start_indices=current_cycles)
            result["diagnostics"].pop("reason", None)
            return result
        x, y = conflict["position"]
        at = conflict["time_s"]
        for member in conflict["members"]:
            restriction = (x, y, max(0, at-_CBS["conflict_window_s"]),
                           at+_CBS["conflict_window_s"], separation+_CBS["constraint_padding_m"])
            updated = (*constraints[member], restriction)
            old_route = current[member]
            start_index, end_index = 0, len(old_route)-1
            if member in current_cycles:
                timeline = _schedule(old_route, speed)
                pivot = current_cycles[member]
                before_cycle = at < timeline[pivot]
                start_index = min(len(old_route)-2, bisect.bisect_left(timeline, max(0, at-_CBS["local_replan_window_s"])))
                end_index = min(len(old_route)-1, bisect.bisect_left(timeline, at+_CBS["local_replan_window_s"]))
                if before_cycle:
                    end_index = min(end_index, pivot)
                    start_index = min(start_index, max(0, end_index-1))
                else:
                    start_index = max(start_index, pivot)
                    end_index = max(end_index, start_index+1)
                if start_index >= end_index or timeline[end_index] <= at:
                    continue
            reroute = plan_path(old_route[start_index], old_route[end_index], obstacles=obstacles, bounds=bounds,
                                temporal_constraints=updated, speed=speed, budget=low_level_budget,
                                start_time_s=_schedule(old_route[:start_index+1], speed)[-1])
            if reroute["status"] != "succeeded":
                continue
            replacement = old_route[:start_index]+reroute["points"]+old_route[end_index+1:]
            candidate = {**current, member: replacement}
            new_cycles = dict(current_cycles)
            if member in new_cycles and end_index <= current_cycles[member]:
                new_cycles[member] += len(reroute["points"])-(end_index-start_index+1)
            if member in new_cycles and (math.dist(replacement[-1][:2], replacement[new_cycles[member]][:2]) > _CBS["closed_route_tolerance_m"] or
                    abs(math.remainder(replacement[-1][2]-replacement[new_cycles[member]][2], math.tau)) > _CBS["closed_heading_tolerance_rad"]):
                continue
            sequence += 1
            heapq.heappush(queue, (sum(len(route) for route in candidate.values()), sequence, candidate,
                                   {**constraints, member: updated}, new_cycles))
    result["diagnostics"]["reason"] = "bounded_conflict_search_exhausted"
    return result
