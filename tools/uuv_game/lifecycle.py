"""Preauthorized boundary turnover and coverage repair, never LLM-controlled spawn."""

import copy
import math

from .config import algorithm_settings
from .algorithms.planning import plan_path, path_safe
from .mission_planning import search_bundle

_LIFECYCLE = algorithm_settings("lifecycle")


def exit_route(runtime, boat):
    x, y = boat["pose"][:2]
    edge = lambda value: min(_LIFECYCLE["exit_goal_edge_max_m"], max(_LIFECYCLE["exit_goal_edge_min_m"], value))
    offset = _LIFECYCLE["exit_goal_offset_m"]
    width, height = runtime.config.width, runtime.config.height
    goals = [[-offset, edge(y), math.pi], [width+offset, edge(y), 0],
             [edge(x), -offset, -math.pi/2], [edge(x), height+offset, math.pi/2]]
    for goal in sorted(goals, key=lambda p: math.dist(p[:2], [x, y])):
        pad = _LIFECYCLE["exit_bounds_padding_m"]
        route = plan_path(boat["pose"], goal, obstacles=runtime.obstacles,
            bounds=(-pad, -pad, width+pad, height+pad), budget=_LIFECYCLE["exit_route_budget"])
        if route["status"] == "succeeded" and route["length_m"]+_LIFECYCLE["exit_route_energy_margin_m"] < boat["remaining_range_m"]:
            return route
    return None


def repair_search(runtime, exclude=(), add=()):
    if not runtime.standing_policy.get("local_repair"):
        runtime.pause("safety_local_repair_authorization_required")
        return False
    boats = [u for u in runtime.uuvs if u["id"] not in exclude and (u["id"] in add or runtime.active.get(u["id"], {}).get("kind") in ("search", "reacquire"))]
    result = search_bundle(boats, runtime.scan_times, runtime.obstacles, runtime.regions,
        allow_partial=runtime.standing_policy["energy_rotation"], now=runtime.sim_time,
        window_s=runtime.config.coverage_window_min*60)
    if result["status"] != "succeeded":
        runtime.event("allocation_blocked", {"reason": result["diagnostics"]})
        runtime.pause("safety_allocation_infeasible")
        return False
    previous = {r["owner"]: r["cells"] for r in runtime.regions}
    for region in result["regions"]:
        member = region["owner"]
        if member not in runtime.active or previous.get(member) != region["cells"] or member in add:
            runtime.active[member] = {"plan_id": runtime.active.get(member, {}).get("plan_id", runtime.standing_policy["plan_id"]), "kind": "search", "phase": "scanning",
                "points": result["routes"][member], "index": 0, "slot": 0, "execution_domain": [0, 0, runtime.config.width, runtime.config.height],
                "cycle_start_index": result.get("cycle_start_indices", {}).get(member, 0),
                "requires_replan_after_cycle": member in result.get("partial_patrols", {}),
                "generation": next(u["generation"] for u in boats if u["id"] == member)}
    runtime.regions = result["regions"]
    runtime.region_revision += 1
    runtime.revision += 1
    runtime.event("coverage_repartitioned", {"regions": len(runtime.regions), "reason": "energy_turnover"})
    return True


def prepare_exits(runtime):
    for boat in runtime.uuvs:
        action = runtime.active.get(boat["id"])
        if not action or action["kind"] == "exit":
            continue
        x, y = boat["pose"][:2]
        if boat["remaining_range_m"] > min(x, y, runtime.config.width-x, runtime.config.height-y)+runtime.config.exit_reserve+_LIFECYCLE["exit_trigger_buffer_m"]:
            continue
        if not runtime.standing_policy["energy_rotation"]:
            runtime.event("energy_authorization_required", {"uuv_id": boat["id"]})
            runtime.pause("safety_energy_authorization_required")
            return False
        route = exit_route(runtime, boat)
        if route is None:
            runtime.pause("safety_exit_infeasible")
            return False
        if not repair_search(runtime, exclude=[boat["id"]]):
            return False
        if action["kind"] == "track":
            runtime.event("tracking_relief_required", {"uuv_id": boat["id"], "contact_id": action.get("contact_id")})
            runtime.queue_agent("A tracking boat is exiting. Plan a replacement team and verify observations; do not report handoff before acquisition.", "energy_exit")
        runtime.active[boat["id"]] = {"plan_id": runtime.standing_policy["plan_id"], "kind": "exit", "phase": "exiting",
            "points": route["points"], "index": 0, "slot": 0, "execution_domain": [-_LIFECYCLE["exit_bounds_padding_m"], -_LIFECYCLE["exit_bounds_padding_m"],
                runtime.config.width+_LIFECYCLE["exit_bounds_padding_m"], runtime.config.height+_LIFECYCLE["exit_bounds_padding_m"]], "generation": boat["generation"]}
        runtime.event("energy_exit_started", {"uuv_id": boat["id"], "generation": boat["generation"], "remaining_range_m": boat["remaining_range_m"]})
    return True


def replacement_pose(runtime, boat, next_poses):
    x, y = next_poses[boat["id"]][:2]
    edge = lambda value: min(_LIFECYCLE["exit_goal_edge_max_m"], max(_LIFECYCLE["exit_goal_edge_min_m"], value))
    inset = _LIFECYCLE["replacement_edge_offset_m"]
    candidates = [[inset, edge(y), 0], [runtime.config.width-inset, edge(y), math.pi],
                  [edge(x), inset, math.pi/2], [edge(x), runtime.config.height-inset, -math.pi/2]]
    for pose in sorted(candidates, key=lambda p: math.dist(p[:2], [x, y])):
        if not path_safe([pose], runtime.obstacles, [0, 0, runtime.config.width, runtime.config.height], margin=_LIFECYCLE["replacement_obstacle_margin_m"]):
            continue
        if all(math.dist(pose[:2], p[:2]) >= _LIFECYCLE["replacement_separation_m"] for key, p in next_poses.items() if key != boat["id"]):
            return pose
    return None


def apply_replacements(runtime, replacements):
    if not replacements:
        return True
    before = (copy.deepcopy(runtime.uuvs), copy.deepcopy(runtime.active), copy.deepcopy(runtime.regions))
    for boat in runtime.uuvs:
        if boat["id"] not in replacements:
            continue
        old_generation = boat["generation"]
        boat.update(pose=replacements[boat["id"]], trail=[], curvature=0, generation=old_generation+1,
                    remaining_range_m=runtime.config.range_capacity)
        runtime.active.pop(boat["id"], None)
    if not repair_search(runtime, add=replacements):
        runtime.uuvs, runtime.active, runtime.regions = before
        return False
    for member in replacements:
        boat = next(u for u in runtime.uuvs if u["id"] == member)
        runtime.metrics["rotation_count"] += 1
        runtime.event("uuv_replenished", {"uuv_id": member, "generation": boat["generation"], "remaining_range_m": boat["remaining_range_m"]})
    return True
