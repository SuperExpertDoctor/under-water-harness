"""Offline seeded three-observer acceptance; no model, contact injection, or teleporting."""

import json
import math

import pytest

from uuv_game.algorithms.planning import path_safe
from uuv_game.config import Config
from uuv_game.runtime import MissionRuntime


def test_search_detect_plan_transit_and_three_boat_passive_fusion(tmp_path):
    runtime = MissionRuntime(tmp_path/"three-observer.sqlite", Config(seed=42))
    try:
        assert not runtime.contacts
        runtime.set_mode("full")
        fleet = runtime.calculate("plan_search", {"standing_policy": True})
        assert fleet["status"] == "succeeded", fleet
        assert runtime.evaluate(fleet["result_id"])["valid"]
        assert runtime.submit(fleet["result_id"], "three-observer-fleet", runtime.episode)["status"] == "active"
        assert len(runtime.regions) == len(runtime.active) == 8
        runtime.start()

        contact = None
        while runtime.sim_time < 1200:
            runtime.tick()
            assert runtime.status == "running", runtime.events[-5:]
            contact = next((item for item in runtime.contacts.values() if item["state"] == "confirmed"), None)
            if contact:
                break
        assert contact is not None, {"sim_s": runtime.sim_time, "contacts": runtime.contacts}
        detected_at = runtime.sim_time
        assert len(contact["hits"]) >= 3
        assert any(sample["source"] == "sensor" and sample["mode"] == "active"
            for sample in runtime.observations if sample["contact_id"] == contact["contact_id"])

        # Explicit membership uses only the measured belief and current own-boat poses.
        nearest = sorted(runtime.uuvs, key=lambda boat: (math.dist(boat["pose"][:2], [contact["x"], contact["y"]]), boat["id"]))[:3]
        members = {boat["id"] for boat in nearest}
        candidate = runtime.calculate("plan_tracking", {"contact_id": contact["contact_id"], "members": sorted(members)})
        assert candidate["status"] == "succeeded", runtime.summary(candidate)
        assert set(candidate["members"]) == members
        routes = runtime.results[candidate["result_id"]]["routes"]
        assert len(routes) == 3
        for boat in nearest:
            route = routes[boat["id"]]
            assert route[0] == pytest.approx(boat["pose"])
            assert len(route) > 2
            assert path_safe(route, runtime.obstacles, [0, 0, 4000, 4000])
        assessment = runtime.evaluate(candidate["result_id"])
        assert assessment["valid"], assessment
        receipt = runtime.submit(candidate["result_id"], "three-observer-track", runtime.episode)
        assert receipt["status"] == "active", receipt
        assert all(runtime.active[member]["phase"] == "transit" for member in members)
        assert len(runtime.regions) == 5
        assert not members & {region["owner"] for region in runtime.regions}
        assert runtime.metrics["effective_tracking_seconds"] == 0

        distance = {member: 0.0 for member in members}
        phases = {member: {"transit"} for member in members}
        triple_streak = 0.0
        triple_seconds = 0.0
        first_triple_at = None
        prior_observation_time = -1
        while runtime.sim_time < 2000:
            previous = {boat["id"]: boat["pose"][:] for boat in nearest}
            events_before = len(runtime.events)
            runtime.tick()
            assert runtime.status == "running", json.dumps({"sim_s": runtime.sim_time, "detected_at_s": detected_at,
                "members": sorted(members), "events": runtime.events[-8:],
                "boats": [{"id": boat["id"], "pose": boat["pose"], "phase": runtime.active[boat["id"]]["phase"]} for boat in nearest]}, sort_keys=True)
            # Acquisition-bridge and active-fallback cycles legitimately mix one active
            # relocation sample into an otherwise all-passive cycle; the bearing-only
            # contract is asserted on steady cycles without those mode transitions.
            transition_cycle = any(event["type"] in ("tracking_active_fallback", "tracking_passive_acquisition_started")
                                   for event in runtime.events[events_before:])
            for boat in nearest:
                movement = math.dist(previous[boat["id"]][:2], boat["pose"][:2])
                assert movement <= runtime.config.speed*runtime.config.dt+1e-6
                assert abs(boat["curvature"]) <= 1/runtime.config.radius+1e-9
                distance[boat["id"]] += movement
                phases[boat["id"]].add(runtime.active[boat["id"]]["phase"])
            assert len(runtime.uuvs) == 8
            assert {member for member, action in runtime.active.items() if action["kind"] == "track"} == members
            if runtime.last_observation_time == prior_observation_time:
                continue
            elapsed = 0 if prior_observation_time < 0 else runtime.last_observation_time-prior_observation_time
            prior_observation_time = runtime.last_observation_time
            samples = [sample for sample in runtime.observations if sample["contact_id"] == contact["contact_id"]
                and sample["time_s"] == prior_observation_time and sample["observer_id"] in members]
            received = {sample["observer_id"] for sample in samples if sample["mode"] == "passive"}
            if received == members and contact["state"] == "tracking" and all(runtime.active[member]["phase"] == "tracking" for member in members):
                assert transition_cycle or all(not ({"x", "y", "range_m"} & sample.keys()) for sample in samples)
                assert all(sample["generation"] == next(boat["generation"] for boat in nearest if boat["id"] == sample["observer_id"])
                    for sample in samples)
                assert contact["geometry_quality"] >= .3
                assert contact["uncertainty_m"] <= 120
                assert contact["tracking_streak"] >= 3
                triple_streak += elapsed
                triple_seconds += elapsed
                if first_triple_at is None:
                    first_triple_at = runtime.sim_time
                if triple_streak >= 3 and runtime.metrics["effective_tracking_seconds"] >= 30:
                    break
            else:
                triple_streak = 0

        evidence = {"seed": 42, "detected_at_s": detected_at, "members": sorted(members),
            "first_three_observer_tracking_s": first_triple_at, "finished_at_s": runtime.sim_time,
            "three_observer_seconds": triple_seconds, "three_observer_streak_s": triple_streak,
            "effective_tracking_seconds": runtime.metrics["effective_tracking_seconds"],
            "phases": {member: sorted(values) for member, values in phases.items()}, "travel_m": distance,
            "contact_state": contact["state"], "geometry_quality": contact["geometry_quality"], "uncertainty_m": contact["uncertainty_m"]}
        assert first_triple_at is not None and first_triple_at > detected_at, evidence
        assert triple_streak >= 3 and runtime.metrics["effective_tracking_seconds"] >= 30, evidence
        assert all({"transit", "tracking"} <= values for values in phases.values()), evidence
        assert all(value > 20 for value in distance.values()), evidence
        assert len(runtime.regions) == 5
        print(json.dumps(evidence, sort_keys=True))
    finally:
        runtime.close()
