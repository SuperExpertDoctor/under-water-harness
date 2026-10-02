"""Builtin plugin: UUV platform control (heading/speed/sensor telemetry).

Owns the motion pipeline stage: per-UUV preferred controls, joint Dubins
validation, adversary stepping, pose integration, boundary turnover.
"""

import math

from ..algorithms.tracking import acquisition_control, tracking_control, follow_path
from ..algorithms.control import choose_controls
from ..algorithms.motion import integrate
from ..capabilities.lifecycle import replacement_pose, apply_replacements
from ..capabilities import adversary as enemy
from ..config import algorithm_settings

_RUNTIME = algorithm_settings("runtime")
_CONTROL = algorithm_settings("control")
_LIFECYCLE = algorithm_settings("lifecycle")
_OBS = algorithm_settings("observations")

PLUGIN = {
    "id": "uuv-control", "name": "UUV 平台控制", "layer": 4, "color": "#475569",
    "desc": "航向 / 速度 / 传感器模式执行与平台遥测",
    "snippet": "执行航向、速度与传感器模式的平台指令",
    "guidelines": ["所有插件的运动意图最终经此输出到平台", "核心算法插件，不可关闭"],
    "inputs": "many", "outputs": "one", "core": True,
    "edges": [],
    "owns_stages": ["motion"],
}


def activity(runtime, ctx):
    for uid in ctx.L["moving"]:
        ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['moving'])} 艇受控" if ctx.L["moving"] else None)


EDGE_SUBJECTS = {}


def stage_motion(rt):
    next_poses = {}
    controls = {}
    for u in rt.uuvs:
        action = rt.active.get(u["id"])
        if not action:
            next_poses[u["id"]] = u["pose"]
            continue
        if action.get("generation", u["generation"]) != u["generation"]:
            rt.pause("safety_stale_generation")
            return False
        pose = u["pose"]
        if action["kind"] == "track":
            estimate = rt.contacts.get(action.get("contact_id"))
            if not estimate or (estimate["state"] == "lost" and not rt.standing_policy["lost_reacquire"]):
                rt.pause("safety_tracking_contact_lost")
                return False
            if estimate["state"] == "lost":
                if action.get("phase") != "transit":
                    action["phase"] = "reacquiring"
                action["acquisition_mode"] = "active"
            elif (action.get("phase") == "tracking" and rt.standing_policy.get("lost_reacquire")
                  and rt.sim_time-estimate["last_seen"] >= _RUNTIME["reacquisition_trigger_s"]):
                action["phase"] = "reacquiring"
                action["acquisition_mode"] = "active"
                rt.event("tracking_reacquisition_started", {"uuv_id": u["id"], "contact_id": action["contact_id"],
                    "last_seen_s": estimate["last_seen"]})
            teammates = [b for b in sorted(rt.uuvs, key=lambda b: (rt.active.get(b["id"], {}).get("slot", 0), b["id"]))
                if rt.active.get(b["id"], {}).get("kind") == "track" and rt.active[b["id"]].get("contact_id") == action["contact_id"]
                and (action.get("phase") == "transit" or rt.active[b["id"]].get("phase") != "transit")]
            slot = next(i for i, b in enumerate(teammates) if b["id"] == u["id"])
            preferred = (acquisition_control(pose, estimate, reacquiring=action.get("phase") == "reacquiring") if action.get("acquisition_mode") == "active" and action.get("phase") in ("provisional", "acquiring", "reacquiring")
                else tracking_control(pose, estimate, slot=slot, team=[b["pose"] for b in teammates] if len(teammates) >= 2 else None))
            if action.get("phase") == "transit" and action.get("points"):
                points = action["points"]
                nearest = min(range(action["index"], min(len(points), action["index"]+_RUNTIME["nearest_waypoint_span"])), key=lambda n: math.dist(pose[:2], points[n][:2]))
                action["index"] = nearest
                if nearest >= len(points)-_RUNTIME["arrival_waypoint_tolerance"] or (math.dist(pose[:2], points[-1][:2]) < _RUNTIME["arrival_distance_m"] and abs(math.remainder(pose[2]-points[-1][2], 2*math.pi)) < _RUNTIME["arrival_heading_rad"]):
                    action["phase"] = "acquiring"
                    action["acquisition_started_at_s"] = rt.sim_time
                    rt.event("tracking_position_reached", {"uuv_id": u["id"], "contact_id": action["contact_id"]})
                else:
                    preferred = follow_path(pose, points[nearest:nearest+_RUNTIME["lookahead_points"]])
        elif action.get("points"):
            points = action["points"]
            index = action["index"]
            end = min(len(points), index+_RUNTIME["nearest_waypoint_span"])
            nearest = min(range(index, end), key=lambda n: math.dist(pose[:2], points[n][:2]))
            action["index"] = nearest
            if nearest >= len(points)-_RUNTIME["cycle_end_waypoint_tolerance"]:
                if action["kind"] == "exit":
                    preferred = 0
                elif math.dist(points[action.get("cycle_start_index", 0)][:2], points[-1][:2]) > _RUNTIME["closed_route_tolerance_m"]:
                    rt.pause("safety_no_authorized_continuation")
                    return False
                else:
                    action["index"] = action.get("cycle_start_index", 0)
                    preferred = follow_path(pose, points[action["index"]:action["index"]+_RUNTIME["lookahead_points"]])
                    if action["kind"] in ("search", "reacquire"):
                        rt.event("search_complete", {"uav_id": u["id"], "plan_id": action["plan_id"]})
                    if action.get("requires_replan_after_cycle"):
                        rt.queue_agent("Coverage gap or overdue revisit detected; review available search assignments.", "search_gap")
            else:
                preferred = follow_path(pose, points[nearest:nearest+_RUNTIME["lookahead_points"]])
        else:
            rt.pause("safety_no_authorized_continuation")
            return False
        domain = action.get("execution_domain", [0, 0, rt.config.width, rt.config.height])
        if action["kind"] != "exit" and min(pose[0], pose[1], rt.config.width-pose[0], rt.config.height-pose[1]) < _CONTROL["obstacle_margin_m"]:
            pad = _CONTROL["obstacle_margin_m"]
            domain = [-pad, -pad, rt.config.width+pad, rt.config.height+pad]
        controls[u["id"]] = {"preferred": preferred, "execution_domain": domain}
    control_diagnostics = {}
    selected = choose_controls(rt.uuvs, controls, rt.contacts, rt.obstacles, separation=rt.config.separation,
        diagnostics=control_diagnostics, recovery_domain=[0, 0, rt.config.width, rt.config.height])
    if selected is None:
        rt.event("control_infeasible", {"algorithm": "joint-dubins-beam", "controls": controls, "diagnostics": control_diagnostics,
            "boats": [{"id": u["id"], "pose": u["pose"], "curvature": u["curvature"]} for u in rt.uuvs]})
        provisional = next((member for member, action in rt.active.items() if action.get("provisional")), None)
        if provisional and rt._end_provisional_contact(provisional, "unsafe_joint_control"):
            rt.tick()
            return False
        rt.pause("safety_infeasible")
        return False
    if control_diagnostics.get("held_boats"):
        rt.event("boats_held", {"uuv_ids": control_diagnostics["held_boats"]})
    for u in rt.uuvs:
        if u["id"] in selected:
            u["curvature"] = selected[u["id"]]
            next_poses[u["id"]] = integrate(u["pose"], rt.config.speed, selected[u["id"]], rt.config.dt)
        else:
            next_poses[u["id"]] = u["pose"]
    # A common tick validates all proposed movements before committing any pose.
    # Pairs already inside the separation line (congestion that slipped in
    # via boundary turnover) must keep opening up; only worsening is a pause.
    for index, first in enumerate(rt.uuvs):
        for second in rt.uuvs[index+1:]:
            next_dist = math.dist(next_poses[first["id"]][:2], next_poses[second["id"]][:2])
            if next_dist < rt.config.separation and next_dist < math.dist(first["pose"][:2], second["pose"][:2]):
                rt.pause("safety_infeasible")
                return False
    target_poses = {}
    for target in rt.targets:
        enemy.sample(rt.adversary, target["pose"], rt.uuvs, rt.obstacles,
                     rt.config.enemy_sensor_range, rt.sim_time, rt.config.seed+rt.frame_id)
        pose, speed, curvature = enemy.control(target["pose"], rt.adversary["detections"],
            rt.adversary["parameters"], rt.obstacles, rt.config, rt.sim_time)
        target_poses[target["id"]] = pose
        # Radiated noise gates passive detection: only a moving target is
        # loud enough to hold a passive track; sprinting is fast but
        # audible, going quiet is stealthy but nearly static.
        target.update(speed=speed, curvature=curvature,
                      passive_signal=speed >= _OBS["passive_signal_min_speed_mps"])
    # Truth is used only by this simulator interlock, never to choose a control.
    collisions = [(u["id"], target["id"]) for u in rt.uuvs for target in rt.targets
                  if math.dist(next_poses[u["id"]][:2], target_poses[target["id"]][:2]) < rt.config.separation]
    if collisions:
        # Hold the boats that would collide and let the quarry keep
        # maneuvering; the tick proceeds so separation opens up over
        # successive ticks instead of pause-looping on frozen geometry.
        for target in rt.targets:
            target["pose"] = target_poses[target["id"]]
        held = {c[0] for c in collisions}
        for u in rt.uuvs:
            if u["id"] in held or any(math.dist(next_poses[u["id"]][:2], t["pose"][:2]) < rt.config.separation for t in rt.targets):
                next_poses[u["id"]] = u["pose"]
                held.add(u["id"])
        # Freezing boats can pull a moving boat's committed pose inside
        # separation of a now-stationary one; freeze worsening pairs too.
        stable = False
        while not stable:
            stable = True
            for index, first in enumerate(rt.uuvs):
                for second in rt.uuvs[index+1:]:
                    next_dist = math.dist(next_poses[first["id"]][:2], next_poses[second["id"]][:2])
                    if next_dist < rt.config.separation and next_dist < math.dist(first["pose"][:2], second["pose"][:2]):
                        next_poses[first["id"]] = first["pose"]
                        next_poses[second["id"]] = second["pose"]
                        held.update((first["id"], second["id"]))
                        stable = False
        rt.event("target_collision_hold", {"collisions": collisions, "held": sorted(held)})
    replacements = {}
    for u in rt.uuvs:
        x, y = next_poses[u["id"]][:2]
        if not 0 <= x <= rt.config.width or not 0 <= y <= rt.config.height:
            if rt.active.get(u["id"], {}).get("kind") != "exit":
                rt.pause("safety_boundary_violation")
                return False
            exiting = {key for key, action in rt.active.items()
                if action.get("kind") == "exit" and key not in replacements}
            replacement = replacement_pose(rt, u, {**next_poses, **replacements}, ignore=exiting)
            if replacement is None or any(math.dist(replacement[:2], p[:2]) < _LIFECYCLE["replacement_separation_m"] for p in target_poses.values()):
                # Hold the crossing boat at the boundary for one tick
                # instead of freezing the whole sim: other boats and
                # targets keep moving, so the entry slot can clear.
                rt.event("exit_entry_blocked", {"uuv_id": u["id"], "pose": u["pose"]})
                next_poses[u["id"]] = u["pose"]
                continue
            replacements[u["id"]] = replacement
    for u in rt.uuvs:
        u["remaining_range_m"] = max(0, u["remaining_range_m"]-math.dist(u["pose"][:2], next_poses[u["id"]][:2]))
        u["pose"] = next_poses[u["id"]]
    apply_replacements(rt, replacements)
    for target in rt.targets:
        target["pose"] = target_poses[target["id"]]
    rt.sim_time = round(rt.sim_time+rt.config.dt, 6)
    rt.frame_id += 1
    return True


STAGES = {"motion": stage_motion}
