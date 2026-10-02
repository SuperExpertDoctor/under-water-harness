"""Preauthorized boundary turnover and coverage repair, never LLM-controlled spawn."""

import math

from ..config import algorithm_settings
from .information import target_evidence_field
from ..algorithms.planning import plan_path, path_safe
from .mission_planning import search_bundle

_LIFECYCLE = algorithm_settings("lifecycle")


def nearest_boundary(pose, width, height):
    x, y = pose[:2]
    return min(([0, y, 0], [width, y, math.pi], [x, 0, math.pi/2], [x, height, -math.pi/2]),
        key=lambda point: math.dist(point[:2], [x, y]))


def navigation_pose(runtime, boat):
    pose = boat["pose"][:]
    width, height = runtime.config.width, runtime.config.height
    inset = _LIFECYCLE["replacement_edge_offset_m"]
    if min(pose[0], pose[1], width-pose[0], height-pose[1]) == 0:
        if pose[0] == 0:
            pose[0] = inset
        elif pose[0] == width:
            pose[0] = width-inset
        elif pose[1] == 0:
            pose[1] = inset
        else:
            pose[1] = height-inset
    return {**boat, "pose": pose}


def exit_route(runtime, boat):
    offset = _LIFECYCLE["exit_goal_offset_m"]
    width, height = runtime.config.width, runtime.config.height
    boundary = nearest_boundary(boat["pose"], width, height)
    goal = [boundary[0]-offset if boundary[0] == 0 else boundary[0]+offset if boundary[0] == width else boundary[0],
            boundary[1]-offset if boundary[1] == 0 else boundary[1]+offset if boundary[1] == height else boundary[1],
            math.remainder(boundary[2]+math.pi, 2*math.pi)]
    pad = _LIFECYCLE["exit_bounds_padding_m"]
    route = plan_path(boat["pose"], goal, obstacles=runtime.obstacles,
        bounds=(-pad, -pad, width+pad, height+pad), budget=_LIFECYCLE["exit_route_budget"])
    if route["status"] == "succeeded" and route["length_m"]+_LIFECYCLE["exit_route_energy_margin_m"] < boat["remaining_range_m"]:
        return route
    return None


def repair_search(runtime, exclude=(), add=(), required=True):
    if not runtime.standing_policy.get("local_repair"):
        runtime.pause("safety_local_repair_authorization_required")
        return False
    boats = [navigation_pose(runtime, u) for u in runtime.uuvs if u["id"] not in exclude and (u["id"] in add or runtime.active.get(u["id"], {}).get("kind") in ("search", "reacquire"))]
    tracked_contacts = {action.get("contact_id") for action in runtime.active.values()
                        if action.get("kind") == "track" and action.get("phase") == "tracking"}
    result = search_bundle(boats, runtime.scan_times, runtime.obstacles, runtime.regions,
        allow_partial=runtime.standing_policy["energy_rotation"], now=runtime.sim_time,
        window_s=runtime.config.coverage_window_min*60,
        target_evidence=target_evidence_field(runtime.scan_times, runtime.contacts, runtime.sim_time,
                                              runtime.config, tracked_contacts))
    if result["status"] != "succeeded":
        runtime.event("allocation_blocked", {"reason": result["diagnostics"], "required": required})
        if required:
            runtime.pause("safety_allocation_infeasible")
        return False
    previous = {r["owner"]: r["cells"] for r in runtime.regions}
    previous_due = {r["owner"]: r.get("scan_cells") for r in runtime.regions}
    for region in result["regions"]:
        member = region["owner"]
        if (member not in runtime.active or previous.get(member) != region["cells"] or member in add
                or previous_due.get(member) != region.get("scan_cells")):
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


def strand(runtime, boat):
    """Maroon a boat that cannot reach any boundary within its remaining range.

    The hull stays visible loitering on its last position instead of deadlocking
    the tick; the rest of the fleet keeps working and the coverage gap is left
    to repair_search/standing plans.
    """
    action = runtime.active.get(boat["id"])
    if action and action["kind"] == "track":
        runtime.event("tracking_relief_required", {"uuv_id": boat["id"], "contact_id": action.get("contact_id")})
        runtime.queue_agent("A tracking boat ran out of range before exiting. Plan a replacement team and verify observations; do not report handoff before acquisition.", "energy_exit")
    runtime.active[boat["id"]] = {"plan_id": runtime.standing_policy["plan_id"], "kind": "stranded", "phase": "stranded",
        "points": [boat["pose"][:]], "index": 0, "slot": 0,
        "execution_domain": [0, 0, runtime.config.width, runtime.config.height], "generation": boat["generation"]}
    runtime.metrics["stranded_count"] = runtime.metrics.get("stranded_count", 0)+1
    runtime.event("uuv_stranded", {"uuv_id": boat["id"], "generation": boat["generation"], "pose": boat["pose"],
        "remaining_range_m": boat["remaining_range_m"], "reason": "exit_route_infeasible"})
    repair_search(runtime, exclude=[boat["id"]], required=False)


def prepare_exits(runtime):
    exiting = sum(1 for action in runtime.active.values() if action.get("kind") == "exit")
    for index, boat in enumerate(runtime.uuvs):
        action = runtime.active.get(boat["id"])
        if not action or action["kind"] in ("exit", "stranded"):
            continue
        x, y = boat["pose"][:2]
        min_dist = min(x, y, runtime.config.width-x, runtime.config.height-y)
        if action.get("relief_member"):
            # finish_handover retires this hull below 1.5*min_dist once the
            # relief pair sustains observations; the normal trigger would fire
            # 500m earlier and always win the race. Hold it until the abort
            # floor so a stalled relief cannot strand the hull either.
            if boat["remaining_range_m"] > min_dist+_LIFECYCLE["relief_abort_margin_m"]:
                continue
        else:
            trigger = (1.5*min_dist+_LIFECYCLE["exit_trigger_buffer_m"]
                       +index*_LIFECYCLE["exit_stagger_step_m"])
            if boat["remaining_range_m"] > trigger:
                continue
            # Exit transits stop painting scan cells; capping how many hulls
            # drain at once keeps queued hulls scanning through turnover waves.
            # Below the forced margin a hull exits anyway rather than strand.
            if (exiting >= _LIFECYCLE["exit_concurrency_limit"]
                    and boat["remaining_range_m"] > 1.5*min_dist+_LIFECYCLE["exit_forced_margin_m"]):
                continue
        if not runtime.standing_policy["energy_rotation"]:
            runtime.event("energy_authorization_required", {"uuv_id": boat["id"]})
            runtime.pause("safety_energy_authorization_required")
            return False
        route = exit_route(runtime, boat)
        if route is None:
            # Straight-line slack underestimates Dubins turn-back length and
            # obstacle detours; retry while cruising buffer remains, then the
            # boat is genuinely lost at sea rather than a tick deadlock.
            if boat["remaining_range_m"] > 1.5*min_dist:
                continue
            strand(runtime, boat)
            continue
        if not repair_search(runtime, exclude=[boat["id"]], required=False):
            runtime.event("coverage_gap_accepted", {"uuv_id": boat["id"], "generation": boat["generation"],
                "reason": "repair_infeasible_for_remaining_fleet"})
        if action.get("relief_member"):
            # The relief never reached a verified pair before this hull hit the
            # abort floor; record the streak it did achieve so mission logs can
            # tell a stalled relief apart from one that never produced samples.
            runtime.event("tracking_handoff_aborted", {"uuv_id": boat["id"], "contact_id": action.get("contact_id"),
                "relief_member": action.get("relief_member"), "relief_streak_s": action.get("relief_streak_s", 0),
                "relief_age_s": runtime.sim_time-action.get("relief_started_s", runtime.sim_time),
                "reason": "relief_floor_reached"})
        if action["kind"] == "track":
            runtime.event("tracking_relief_required", {"uuv_id": boat["id"], "contact_id": action.get("contact_id")})
            runtime.queue_agent("A tracking boat is exiting. Plan a replacement team and verify observations; do not report handoff before acquisition.", "energy_exit")
        boundary = nearest_boundary(boat["pose"], runtime.config.width, runtime.config.height)
        runtime.active[boat["id"]] = {"plan_id": runtime.standing_policy["plan_id"], "kind": "exit", "phase": "exiting", "exit_point": boundary[:2],
            "points": route["points"], "index": 0, "slot": 0, "execution_domain": [-_LIFECYCLE["exit_bounds_padding_m"], -_LIFECYCLE["exit_bounds_padding_m"],
                runtime.config.width+_LIFECYCLE["exit_bounds_padding_m"], runtime.config.height+_LIFECYCLE["exit_bounds_padding_m"]], "generation": boat["generation"]}
        exiting += 1
        runtime.event("energy_exit_started", {"uuv_id": boat["id"], "generation": boat["generation"], "remaining_range_m": boat["remaining_range_m"], "exit_point": boundary[:2]})
    return True


def replacement_pose(runtime, boat, next_poses, ignore=frozenset()):
    start, end = boat["pose"], next_poses[boat["id"]]
    width, height = runtime.config.width, runtime.config.height
    crossings = []
    for axis, boundary, heading in ((0, 0, 0), (0, width, math.pi),
                                    (1, 0, math.pi/2), (1, height, -math.pi/2)):
        delta = end[axis]-start[axis]
        if delta == 0:
            continue
        fraction = (boundary-start[axis])/delta
        other = start[1-axis]+fraction*(end[1-axis]-start[1-axis])
        if 0 <= fraction <= 1 and 0 <= other <= (height if axis == 0 else width):
            crossings.append((fraction, [boundary, other, heading] if axis == 0 else [other, boundary, heading]))
    if not crossings:
        return None
    pose = min(crossings, key=lambda crossing: crossing[0])[1]
    margin = _LIFECYCLE["replacement_obstacle_margin_m"]
    # Boats still held at the boundary waiting to exit are not traffic: if
    # they counted, hulls crossing within separation of each other would pin
    # one another's replacement forever. Spawned replacements are not in
    # ignore and still enforce separation between simultaneous entries.
    if path_safe([pose], runtime.obstacles, [-margin, -margin, runtime.config.width+margin, runtime.config.height+margin], margin=margin) and all(
            math.dist(pose[:2], p[:2]) >= _LIFECYCLE["replacement_separation_m"]
            for key, p in next_poses.items() if key != boat["id"] and key not in ignore):
        return pose
    return None


def apply_replacements(runtime, replacements):
    if not replacements:
        return True
    for boat in runtime.uuvs:
        if boat["id"] not in replacements:
            continue
        old_generation = boat["generation"]
        boat.update(pose=replacements[boat["id"]], trail=[], curvature=0, generation=old_generation+1,
                    remaining_range_m=runtime.config.range_capacity)
        runtime.active.pop(boat["id"], None)
    if not repair_search(runtime, add=replacements, required=False):
        runtime.repair_pending.update(replacements)
        runtime.event("coverage_gap_accepted", {"uuv_ids": list(replacements),
            "reason": "repair_infeasible_for_remaining_fleet"})
    for member in replacements:
        boat = next(u for u in runtime.uuvs if u["id"] == member)
        runtime.metrics["rotation_count"] += 1
        runtime.event("uuv_replenished", {"uuv_id": member, "generation": boat["generation"], "remaining_range_m": boat["remaining_range_m"],
            "exit_point": boat["pose"][:2], "entry_point": boat["pose"][:2]})
    return True
