"""Preauthorized boundary turnover and coverage repair, never LLM-controlled spawn."""

import copy
import math

from .algorithms.planning import plan_path, path_safe
from .mission_planning import search_bundle


def exit_route(runtime, boat):
    x, y = boat["pose"][:2]
    goals = [[-30, min(3800, max(200, y)), math.pi], [4030, min(3800, max(200, y)), 0],
             [min(3800, max(200, x)), -30, -math.pi/2], [min(3800, max(200, x)), 4030, math.pi/2]]
    for goal in sorted(goals, key=lambda p: math.dist(p[:2], [x, y])):
        route = plan_path(boat["pose"], goal, obstacles=runtime.obstacles, bounds=(-100, -100, 4100, 4100), budget=30)
        if route["status"] == "succeeded" and route["length_m"]+100 < boat["remaining_range_m"]:
            return route
    return None


def repair_search(runtime, exclude=(), add=()):
    if not runtime.standing_policy.get("local_repair"):
        runtime.pause("safety_local_repair_authorization_required")
        return False
    boats = [u for u in runtime.uuvs if u["id"] not in exclude and (u["id"] in add or runtime.active.get(u["id"], {}).get("kind") in ("search", "reacquire"))]
    result = search_bundle(boats, runtime.scan_times, runtime.obstacles, runtime.regions, allow_partial=runtime.standing_policy["energy_rotation"])
    if result["status"] != "succeeded":
        runtime.event("allocation_blocked", {"reason": result["diagnostics"]})
        runtime.pause("safety_allocation_infeasible")
        return False
    previous = {r["owner"]: r["cells"] for r in runtime.regions}
    for region in result["regions"]:
        member = region["owner"]
        if member not in runtime.active or previous.get(member) != region["cells"] or member in add:
            runtime.active[member] = {"plan_id": runtime.active.get(member, {}).get("plan_id", runtime.standing_policy["plan_id"]), "kind": "search", "phase": "scanning",
                "points": result["routes"][member], "index": 0, "slot": 0, "execution_domain": [0, 0, 4000, 4000],
                "cycle_start_index": result.get("cycle_start_indices", {}).get(member, 0),
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
        if boat["remaining_range_m"] > min(x, y, 4000-x, 4000-y)+runtime.config.exit_reserve+500:
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
            "points": route["points"], "index": 0, "slot": 0, "execution_domain": [-100, -100, 4100, 4100], "generation": boat["generation"]}
        runtime.event("energy_exit_started", {"uuv_id": boat["id"], "generation": boat["generation"], "remaining_range_m": boat["remaining_range_m"]})
    return True


def replacement_pose(runtime, boat, next_poses):
    x, y = next_poses[boat["id"]][:2]
    candidates = [[40, min(3800, max(200, y)), 0], [3960, min(3800, max(200, y)), math.pi],
                  [min(3800, max(200, x)), 40, math.pi/2], [min(3800, max(200, x)), 3960, -math.pi/2]]
    for pose in sorted(candidates, key=lambda p: math.dist(p[:2], [x, y])):
        if not path_safe([pose], runtime.obstacles, [0, 0, 4000, 4000], margin=20):
            continue
        if all(math.dist(pose[:2], p[:2]) >= 150 for key, p in next_poses.items() if key != boat["id"]):
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
