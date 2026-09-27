"""Focused sensor fixtures; genuine-motion acceptance is tested separately."""

import random
from types import SimpleNamespace

import pytest

from uuv_game.config import Config
from uuv_game.algorithms.tracking import tracking_plan
from uuv_game.runtime import MissionRuntime
from uuv_game.sensing import observe, sensor_mode


@pytest.mark.parametrize("size", [2, 3])
@pytest.mark.parametrize("phase", ["acquiring", "reacquiring"])
def test_active_bridge_requires_measured_geometry_then_fresh_passive_streak(size, phase):
    boats = [{"id": f"UUV-{i+1}", "pose": pose, "generation": 0, "capabilities": ["active", "passive"]}
        for i, pose in enumerate(([1800, 2000, 0], [2000, 1800, 0], [2200, 2000, 0])[:size])]
    events = []
    runtime = SimpleNamespace(sim_time=1, last_observation_time=-1, contacts={},
        targets=[{"id": "target", "pose": [2000, 2000, 0]}], contact_mapping={}, uuvs=boats,
        sensor_enabled=True, active={boat["id"]: {"kind": "track", "phase": "transit",
            "contact_id": "CONTACT-1", "acquisition_mode": "active"} for boat in boats},
        config=Config(), obstacles=[], rng=random.Random(42), contact_counter=0,
        observation_cursor=0, observations=[], event=lambda name, data: events.append((name, data)),
        queue_agent=lambda *args: None, metrics={"effective_tracking_seconds": 0, "lost_seconds": 0},
        scan_times=[[-1]*40 for _ in range(40)])
    observe(runtime)
    assert runtime.contacts, "Active acquisition must generate real measured range/bearing"
    assert all(sensor_mode(action) == "active" for action in runtime.active.values())
    assert runtime.metrics["effective_tracking_seconds"] == 0
    for action in runtime.active.values():
        action["phase"] = phase
    runtime.sim_time = 2
    observe(runtime)
    assert all(sensor_mode(action) == "passive" for action in runtime.active.values())
    assert all(sample["mode"] == "active" for sample in runtime.observations if sample["time_s"] == 2)
    assert runtime.scan_times[18][19] == 2, "Coverage uses the modes that produced this cycle's observations"
    assert runtime.contacts["CONTACT-1"]["tracking_streak"] == 0
    assert runtime.metrics["effective_tracking_seconds"] == 0
    for now in (3, 4):
        runtime.sim_time = now
        observe(runtime)
        assert runtime.metrics["effective_tracking_seconds"] == 0
    runtime.sim_time = 5
    observe(runtime)
    assert runtime.contacts["CONTACT-1"]["state"] == "tracking"
    assert runtime.contacts["CONTACT-1"]["tracking_streak"] == 3
    assert runtime.metrics["effective_tracking_seconds"] == 1
    assert all(sample["mode"] == "passive" and "range_m" not in sample
        for sample in runtime.observations if sample["time_s"] >= 3)
    assert any(name == "tracking_passive_acquisition_started" for name, _ in events)


def test_new_team_activates_bridge_but_established_team_addition_stays_passive(tmp_path):
    runtime = MissionRuntime(tmp_path/"bridge.sqlite")
    try:
        plan = {"plan_id": "plan-unit", "kind": "track", "members": ["UUV-1", "UUV-2"],
            "contact_id": "CONTACT-unit", "execution_domain": [0, 0, 4000, 4000],
            "acquisition_mode": "active_until_cooperative_geometry"}
        runtime._activate(plan)
        assert all(sensor_mode(runtime.active[member]) == "active" for member in plan["members"])
        # Unit fixture represents a previously established passive pair, not acceptance evidence.
        runtime.contacts["CONTACT-unit"] = {"state": "tracking"}
        for action in runtime.active.values():
            action.update(phase="tracking", acquisition_mode="passive")
        incoming = {**plan, "plan_id": "plan-relief", "members": ["UUV-3"]}
        runtime.uuvs[2]["capabilities"] = ["passive"]
        assessment = runtime._assessment({**incoming, "result_id": "unit-result", "episode_id": runtime.episode, "status": "succeeded",
            "start_poses": {"UUV-3": runtime.uuvs[2]["pose"]}})
        assert assessment["valid"], assessment
        runtime._activate(incoming)
        assert all(sensor_mode(action) == "passive" for action in runtime.active.values())
        runtime._activate(plan)
        assert all(sensor_mode(runtime.active[member]) == "active" for member in plan["members"])
    finally:
        runtime.close()


@pytest.mark.parametrize("established", [False, True])
def test_active_bridge_capability_required_for_new_or_replaced_group(tmp_path, established):
    runtime = MissionRuntime(tmp_path/"capability.sqlite")
    try:
        # Unit eligibility fixture, not generated-contact acceptance evidence.
        runtime.contacts["CONTACT-unit"] = {"x": 700, "y": 900, "vx": 1, "vy": 0,
            "uncertainty_m": 10, "state": "tracking" if established else "confirmed"}
        for boat in runtime.uuvs[:2]:
            boat["capabilities"] = ["passive"]
            if established:
                runtime.active[boat["id"]] = {"plan_id": "old", "kind": "track", "phase": "tracking",
                    "contact_id": "CONTACT-unit", "acquisition_mode": "passive"}
        candidate = runtime.calculate("plan_tracking", {"contact_id": "CONTACT-unit", "members": ["UUV-1", "UUV-2"]})
        assert candidate["status"] == "infeasible", candidate
        assert candidate["diagnostics"]["reason"] == "active_acquisition_requires_two_active_sensors"
        assessment = runtime._assessment({**runtime.results[candidate["result_id"]], "status": "succeeded",
            "members": ["UUV-1", "UUV-2"], "start_poses": {boat["id"]: boat["pose"] for boat in runtime.uuvs[:2]},
            "assignments": {boat["id"]: runtime.active.get(boat["id"], {}).get("plan_id") for boat in runtime.uuvs[:2]}})
        assert "active_acquisition_requires_two_active_sensors" in assessment["errors"]
        assert candidate["acquisition_requirements"] == {"min_active_observers": 2, "eligible_members": []}
    finally:
        runtime.close()


def test_auto_selection_uses_feasible_active_pair_and_quiet_relief_is_explicit():
    boats = [{"id": str(i), "pose": pose, "remaining_range_m": 18000,
        "capabilities": ["passive"] if i < 2 else ["active", "passive"]}
        for i, pose in enumerate(([1800, 2000, 0], [2000, 1800, 0], [400, 400, 0], [400, 1400, 0], [400, 2400, 0]))]
    contact = {"x": 2200, "y": 2000, "vx": .5, "vy": .4, "uncertainty_m": 10}
    candidate = tracking_plan(boats, contact, [])
    assert candidate["status"] == "succeeded", candidate
    assert len({"2", "3", "4"}.intersection(candidate["members"])) >= 2
    assert tracking_plan(boats[:2], contact, [])["status"] == "infeasible"
    quiet = tracking_plan(boats[:2], contact, [], require_active_acquisition=False)
    assert quiet["status"] == "succeeded", quiet
    assert quiet["acquisition_mode"] == "passive_existing_team"
