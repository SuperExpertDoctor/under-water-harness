"""Standing-policy relief for a two-boat team; success requires real acquisition."""

import copy
import math

from .algorithms.tracking import tracking_plan
from .config import algorithm_settings
from .information import target_evidence_field
from .lifecycle import exit_route, nearest_boundary
from .mission_planning import search_bundle

_HANDOVER = algorithm_settings("handover")
_LIFECYCLE = algorithm_settings("lifecycle")


def _authorized(runtime):
    return all(runtime.standing_policy.get(key) for key in ("enabled", "energy_rotation", "local_repair"))


def _reserve(runtime, boat, margin):
    x, y = boat["pose"][:2]
    return min(x, y, runtime.config.width-x, runtime.config.height-y)+runtime.config.exit_reserve+margin


def prepare_handover(runtime):
    """Prepare at most one relief every caller-controlled 25-tick interval.

    Three-member teams deliberately defer to normal reserve-triggered exit;
    adding a fourth observer is never permitted by this bounded policy.
    """
    if not _authorized(runtime):
        return False
    with runtime.lock:
        for departing in runtime.uuvs:
            action = runtime.active.get(departing["id"], {})
            if action.get("kind") != "track" or action.get("relief_member") or departing["remaining_range_m"] > _reserve(runtime, departing, _HANDOVER["early_relief_range_margin_m"]):
                continue
            contact_id = action.get("contact_id")
            contact = runtime.contacts.get(contact_id)
            plan = runtime.plans.get(action.get("plan_id"))
            team = [boat for boat in runtime.uuvs if runtime.active.get(boat["id"], {}).get("kind") == "track"
                    and runtime.active[boat["id"]].get("contact_id") == contact_id]
            if not contact or contact["state"] in ("tentative", "lost") or not plan or plan["status"] != "active" or len(team) != 2:
                continue
            survivor = next(boat for boat in team if boat["id"] != departing["id"])
            if any(runtime.active[boat["id"]].get("generation") != boat["generation"] or
                   runtime.active[boat["id"]].get("plan_id") != plan["plan_id"] for boat in team):
                continue
            candidates = [boat for boat in runtime.uuvs if runtime.active.get(boat["id"], {}).get("kind") in ("search", "reacquire")
                and "passive" in boat.get("capabilities", []) and boat["remaining_range_m"] > _reserve(runtime, boat, _HANDOVER["candidate_range_margin_m"])]
            candidates.sort(key=lambda boat: (-boat["remaining_range_m"], math.dist(boat["pose"][:2], [contact["x"], contact["y"]]), boat["id"]))
            for incoming in candidates[:_HANDOVER["candidate_limit"]]:
                transit = tracking_plan([survivor, incoming], contact, runtime.obstacles, require_active_acquisition=False)
                if transit["status"] != "succeeded":
                    continue
                arrival_cost = transit["slots"][incoming["id"]]["length_m"]+_HANDOVER["arrival_time_margin_s"]*runtime.config.speed
                exit_candidates = [departing, survivor, {**incoming, "pose": transit["slots"][incoming["id"]]["pose"]}]
                exit_routes = [exit_route(runtime, boat) for boat in exit_candidates]
                if any(route is None or boat["remaining_range_m"] <= arrival_cost+route["length_m"]+runtime.config.exit_reserve+margin
                       for boat, route, margin in zip(exit_candidates, exit_routes, (0, _HANDOVER["member_exit_margin_m"], _HANDOVER["member_exit_margin_m"]))):
                    continue
                searching = [boat for boat in runtime.uuvs if boat["id"] != incoming["id"] and
                    runtime.active.get(boat["id"], {}).get("kind") in ("search", "reacquire")]
                tracked_contacts = {a.get("contact_id") for a in runtime.active.values()
                                    if a.get("kind") == "track" and a.get("phase") == "tracking"}
                repair = search_bundle(searching, runtime.scan_times, runtime.obstacles, runtime.regions,
                    allow_partial=True, now=runtime.sim_time, window_s=runtime.config.coverage_window_min*60,
                    target_evidence=target_evidence_field(runtime.scan_times, runtime.contacts, runtime.sim_time,
                                                          runtime.config, tracked_contacts))
                if repair["status"] != "succeeded":
                    continue
                # All geometry and coverage calculations finish before any assignment changes.
                with runtime.transaction():
                    previous = {region["owner"]: region["cells"] for region in runtime.regions}
                    for region in repair["regions"]:
                        member = region["owner"]
                        if previous.get(member) != region["cells"]:
                            runtime.active[member].update(points=repair["routes"][member], index=0,
                                execution_domain=[0, 0, runtime.config.width, runtime.config.height], cycle_start_index=repair.get("cycle_start_indices", {}).get(member, 0))
                    prior_plan = runtime.plans.get(runtime.active[incoming["id"]].get("plan_id"))
                    if prior_plan:
                        prior_plan["active_members"] = [member for member in prior_plan.get("active_members", prior_plan["members"]) if member != incoming["id"]]
                    runtime.active[incoming["id"]] = {"plan_id": plan["plan_id"], "kind": "track", "phase": "transit",
                        "contact_id": contact_id, "points": transit["routes"][incoming["id"]], "index": 0, "slot": 2,
                        "execution_domain": [0, 0, runtime.config.width, runtime.config.height], "generation": incoming["generation"]}
                    action.update(relief_member=incoming["id"], relief_generation=incoming["generation"],
                        relief_survivor=survivor["id"], relief_survivor_generation=survivor["generation"],
                        relief_started_s=runtime.sim_time, relief_streak_s=0, relief_last_sample_s=None)
                    plan["active_members"] = [boat["id"] for boat in team]+[incoming["id"]]
                    plan["active_generations"] = {boat["id"]: boat["generation"] for boat in team+[incoming]}
                    runtime.regions = copy.deepcopy(repair["regions"])
                    runtime.region_revision += 1
                    runtime.revision += 1
                    if runtime.metrics.get("handoff_attempts") is not None:
                        runtime.metrics["handoff_attempts"] += 1
                    runtime.event("coverage_repartitioned", {"regions": len(runtime.regions), "reason": "tracking_handover"})
                    runtime.event("tracking_handoff_started", {"contact_id": contact_id, "plan_id": plan["plan_id"],
                        "departing": departing["id"], "incoming": incoming["id"], "generation": incoming["generation"],
                        "authorization_plan_id": runtime.standing_policy["plan_id"], "status": "transit"})
                return True
    return False


def finish_handover(runtime):
    """Call after observation generation; duplicate/stale samples cannot add time."""
    if not _authorized(runtime):
        return False
    with runtime.lock:
        boats = {boat["id"]: boat for boat in runtime.uuvs}
        for departing_id, action in list(runtime.active.items()):
            if action.get("kind") != "track" or not action.get("relief_member"):
                continue
            contact = runtime.contacts.get(action.get("contact_id"))
            pair = [(action["relief_member"], action["relief_generation"]),
                    (action["relief_survivor"], action["relief_survivor_generation"])]
            valid = contact and contact["state"] not in ("tentative", "lost") and contact["uncertainty_m"] <= _HANDOVER["maximum_contact_uncertainty_m"]
            valid = valid and boats[departing_id]["generation"] == action.get("generation")
            for member, generation in pair:
                active = runtime.active.get(member, {})
                valid = valid and member in boats and boats[member]["generation"] == generation and active.get("generation") == generation
                valid = valid and boats[member]["remaining_range_m"] > _reserve(runtime, boats[member], _HANDOVER["member_live_margin_m"])
                valid = valid and active.get("kind") == "track" and active.get("contact_id") == action["contact_id"]
                valid = valid and active.get("phase") in ("acquiring", "tracking")
            observations = []
            if valid:
                for member, generation in pair:
                    sample = next((sample for sample in reversed(runtime.observations) if sample.get("observer_id") == member and
                        sample.get("generation") == generation and sample.get("contact_id") == action["contact_id"] and
                        sample.get("mode") == "passive" and 0 <= runtime.sim_time-sample["time_s"] <= _HANDOVER["maximum_observation_age_s"]), None)
                    if sample:
                        observations.append(sample)
            geometry = abs(math.sin(observations[0]["bearing_rad"]-observations[1]["bearing_rad"])) if len(observations) == 2 else 0
            if not valid or geometry <= _HANDOVER["minimum_geometry_quality"]:
                action.update(relief_streak_s=0, relief_last_sample_s=None)
                continue
            observed_at = min(sample["time_s"] for sample in observations)
            previous = action.get("relief_last_sample_s")
            if previous is not None and observed_at <= previous:
                continue
            action["relief_streak_s"] = action.get("relief_streak_s", 0)+(observed_at-previous) if previous is not None and observed_at-previous <= _HANDOVER["maximum_observation_gap_s"] else 0
            action["relief_last_sample_s"] = observed_at
            if action["relief_streak_s"] < _HANDOVER["acquisition_streak_s"]:
                continue
            departing = boats[departing_id]
            boundary = nearest_boundary(departing["pose"], runtime.config.width, runtime.config.height)
            if departing["remaining_range_m"] > 1.5*math.dist(departing["pose"][:2], boundary[:2]):
                continue
            route = exit_route(runtime, departing)
            if route is None:
                continue
            with runtime.transaction():
                runtime.active[departing_id] = {"plan_id": runtime.standing_policy["plan_id"], "kind": "exit", "phase": "exiting",
                    "exit_point": boundary[:2], "points": route["points"], "index": 0, "slot": 0, "execution_domain": [
                        -_LIFECYCLE["exit_bounds_padding_m"], -_LIFECYCLE["exit_bounds_padding_m"],
                        runtime.config.width+_LIFECYCLE["exit_bounds_padding_m"],
                        runtime.config.height+_LIFECYCLE["exit_bounds_padding_m"]], "generation": departing["generation"]}
                plan = runtime.plans.get(action["plan_id"])
                if plan:
                    plan["active_members"] = [member for member, _ in pair]
                    plan["active_generations"] = dict(pair)
                runtime.metrics["handoff_count"] += 1
                runtime.revision += 1
                runtime.event("tracking_handoff_completed", {"contact_id": action["contact_id"], "plan_id": action["plan_id"],
                    "departing": departing_id, "incoming": action["relief_member"], "observers": [member for member, _ in pair],
                    "geometry_quality": geometry, "sustained_seconds": action["relief_streak_s"],
                    "authorization_plan_id": runtime.standing_policy["plan_id"]})
                runtime.event("energy_exit_started", {"uuv_id": departing_id, "generation": departing["generation"],
                    "remaining_range_m": departing["remaining_range_m"], "exit_point": boundary[:2], "reason": "tracking_handover"})
            return True
    return False
