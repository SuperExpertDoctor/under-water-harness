"""Focused sensor fixtures; genuine-motion acceptance is tested separately."""

import math
import random
from types import SimpleNamespace

import pytest
import uuv_game.runtime as runtime_module

from uuv_game.config import Config
from uuv_game.algorithms import tracking
from uuv_game.algorithms.tracking import tracking_plan
from uuv_game.observations import initialize
from uuv_game.runtime import MissionRuntime
from uuv_game.sensing import observe, sensor_mode, sensor_roles, side_scan_contains


def test_tracking_entry_pose_faces_predicted_contact_inside_active_cone():
    boats = [{"id": "UUV-1", "pose": [900, 1500, 0], "remaining_range_m": 18000},
        {"id": "UUV-2", "pose": [1500, 900, math.pi/2], "remaining_range_m": 18000}]
    contact = {"x": 1800, "y": 1700, "vx": 0.5, "vy": 0.4, "uncertainty_m": 15}
    plan = tracking_plan(boats, contact, [])
    assert plan["status"] == "succeeded", plan
    for slot in plan["slots"].values():
        x, y, heading = slot["pose"]
        future = [contact["x"]+contact["vx"]*slot["eta_s"], contact["y"]+contact["vy"]*slot["eta_s"]]
        bearing = math.atan2(future[1]-y, future[0]-x)
        assert abs(math.remainder(bearing-heading, math.tau)) <= math.radians(Config().forward_active_half_angle_deg)


def test_active_acquisition_steers_toward_estimate_with_bounded_curvature():
    pose = [1800, 2000, math.pi]
    estimate = {"x": 2000, "y": 2000, "vx": 0, "vy": 0, "uncertainty_m": 15}
    curvature = tracking.acquisition_control(pose, estimate)
    assert abs(curvature) <= 1/Config().radius
    assert abs(math.remainder(math.atan2(estimate["y"]-pose[1], estimate["x"]-pose[0])-pose[2]-curvature*Config().speed*3, math.tau)) < math.pi


def test_lost_contact_does_not_cancel_unfinished_support_transit(tmp_path):
    runtime = MissionRuntime(tmp_path/"transit-loss.sqlite")
    try:
        runtime.targets = []
        runtime.contacts["CONTACT-1"] = {**initialize(1500, 1700, 0, 15), "contact_id": "CONTACT-1",
            "state": "lost", "last_seen": 0, "hits": [], "samples": [], "tracking_streak": 0, "observers": []}
        runtime.standing_policy["lost_reacquire"] = True
        pose = runtime.uuvs[0]["pose"][:]
        runtime.active["UUV-1"] = {"plan_id": "plan-unit", "kind": "track", "phase": "transit",
            "contact_id": "CONTACT-1", "generation": 1, "slot": 0, "index": 0,
            "points": [[pose[0]+50*i, pose[1], pose[2]] for i in range(9)], "acquisition_mode": "active",
            "execution_domain": [0, 0, runtime.config.width, runtime.config.height]}
        runtime.start()
        runtime.tick()
        assert runtime.status == "running"
        assert runtime.active["UUV-1"]["phase"] == "transit"
    finally:
        runtime.close()


def test_single_recovered_fix_does_not_end_team_reacquisition(tmp_path):
    runtime = MissionRuntime(tmp_path/"single-fix.sqlite")
    try:
        runtime.targets = []
        runtime.contacts["CONTACT-1"] = {**initialize(1500, 1700, 0, 15), "contact_id": "CONTACT-1",
            "state": "degraded", "last_seen": 0, "hits": [], "samples": [], "tracking_streak": 0, "observers": ["UUV-1"]}
        runtime.standing_policy["lost_reacquire"] = True
        for boat in runtime.uuvs[:2]:
            runtime.active[boat["id"]] = {"kind": "track", "phase": "reacquiring", "plan_id": "track-plan",
                "contact_id": "CONTACT-1", "generation": boat["generation"], "acquisition_mode": "active",
                "execution_domain": [0, 0, runtime.config.width, runtime.config.height]}
        runtime.start()
        runtime.tick()
        assert runtime.status == "running"
        assert all(runtime.active[boat["id"]]["phase"] == "reacquiring" for boat in runtime.uuvs[:2])
    finally:
        runtime.close()


def test_stale_passive_bearings_start_active_reacquisition_before_contact_lost(tmp_path):
    runtime = MissionRuntime(tmp_path/"stale-bearings.sqlite")
    try:
        runtime.targets = []
        runtime.contacts["CONTACT-1"] = {**initialize(1500, 1700, 0, 15), "contact_id": "CONTACT-1",
            "state": "degraded", "last_seen": 0, "hits": [], "samples": [], "tracking_streak": 0, "observers": []}
        runtime.standing_policy["lost_reacquire"] = True
        runtime.active["UUV-1"] = {"kind": "track", "phase": "tracking", "plan_id": "track-plan",
            "contact_id": "CONTACT-1", "generation": 1, "acquisition_mode": "passive",
            "execution_domain": [0, 0, runtime.config.width, runtime.config.height]}
        runtime.sim_time = 4
        runtime.start()
        runtime.tick()
        assert runtime.active["UUV-1"]["phase"] == "reacquiring"
        assert sensor_mode(runtime.active["UUV-1"]) == "active"
    finally:
        runtime.close()


def test_delayed_tracking_approval_rejects_changed_contact_estimate(tmp_path):
    runtime = MissionRuntime(tmp_path/"approval.sqlite")
    try:
        contact = {**initialize(1800, 1700, 1, 15), "contact_id": "CONTACT-1",
            "state": "confirmed", "last_seen": 1, "hits": [1], "samples": [], "tracking_streak": 0, "observers": ["UUV-1"]}
        runtime.contacts["CONTACT-1"] = contact
        candidate = runtime.calculate("plan_tracking", {"contact_id": "CONTACT-1"})
        assert candidate["status"] == "succeeded", candidate
        assert runtime.evaluate(candidate["result_id"])["valid"]
        contact["x"] += 250
        assert "contact_estimate_changed_replan" in runtime.evaluate(candidate["result_id"])["errors"]
    finally:
        runtime.close()


def test_stale_lost_belief_releases_team_to_preapproved_search(tmp_path):
    runtime = MissionRuntime(tmp_path/"expired-belief.sqlite")
    try:
        runtime.submit(runtime.calculate("plan_search", {"standing_policy": True})["result_id"], "start", runtime.episode)
        runtime.contacts["CONTACT-1"] = {**initialize(1800, 1700, 0, 15), "contact_id": "CONTACT-1",
            "state": "lost", "last_seen": 0, "hits": [], "samples": [], "tracking_streak": 0, "observers": []}
        for member in ("UUV-1", "UUV-2"):
            runtime.active[member] = {"kind": "track", "phase": "reacquiring", "plan_id": "track-plan",
                "contact_id": "CONTACT-1", "generation": 1, "acquisition_mode": "active"}
        runtime.regions = [region for region in runtime.regions if region["owner"] not in ("UUV-1", "UUV-2")]
        runtime.sim_time = 200
        runtime.start()
        runtime.tick()
        assert runtime.status == "running"
        assert all(runtime.active[member]["kind"] == "search" for member in ("UUV-1", "UUV-2"))
        assert len(runtime.regions) == 8
        assert any(event["type"] == "stale_contact_search_resumed" for event in runtime.events)
    finally:
        runtime.close()


def test_lost_belief_timeout_does_not_cancel_support_in_transit(tmp_path):
    runtime = MissionRuntime(tmp_path/"transit-timeout.sqlite")
    try:
        runtime.targets = []
        runtime.contacts["CONTACT-1"] = {**initialize(1500, 1700, 0, 15), "contact_id": "CONTACT-1",
            "state": "lost", "last_seen": 0, "hits": [], "samples": [], "tracking_streak": 0, "observers": []}
        runtime.standing_policy["lost_reacquire"] = True
        runtime.standing_policy["local_repair"] = True
        pose = runtime.uuvs[0]["pose"]
        runtime.active["UUV-1"] = {"kind": "track", "phase": "transit", "plan_id": "approved-team",
            "contact_id": "CONTACT-1", "generation": 1, "index": 0,
            "points": [[pose[0]+50*i, pose[1], pose[2]] for i in range(9)],
            "execution_domain": [0, 0, runtime.config.width, runtime.config.height]}
        runtime.sim_time = 200
        runtime.start()
        runtime.tick()
        assert runtime.status == "running"
        assert runtime.active["UUV-1"]["phase"] == "transit"
    finally:
        runtime.close()


def test_arrived_support_gets_bounded_active_acquisition_window(tmp_path):
    runtime = MissionRuntime(tmp_path/"arrival-lease.sqlite")
    try:
        runtime.targets = []
        runtime.contacts["CONTACT-1"] = {**initialize(1500, 1700, 0, 15), "contact_id": "CONTACT-1",
            "state": "lost", "last_seen": 0, "hits": [], "samples": [], "tracking_streak": 0, "observers": []}
        runtime.standing_policy["lost_reacquire"] = True
        runtime.standing_policy["local_repair"] = True
        runtime.active["UUV-1"] = {"kind": "track", "phase": "acquiring", "plan_id": "approved-team",
            "contact_id": "CONTACT-1", "generation": 1, "acquisition_mode": "active",
            "acquisition_started_at_s": 160, "execution_domain": [0, 0, runtime.config.width, runtime.config.height]}
        runtime.sim_time = 200
        runtime.start()
        runtime.tick()
        assert runtime.status == "running"
        assert runtime.active["UUV-1"]["kind"] == "track"
    finally:
        runtime.close()


@pytest.mark.parametrize("grant", [False, True])
def test_first_observer_holds_contact_only_with_startup_grant(tmp_path, grant):
    runtime = MissionRuntime(tmp_path/"discovery.sqlite")
    try:
        runtime.set_mode("request")
        initial = runtime.calculate("plan_search", {"standing_policy": True})
        pending = runtime.submit(initial["result_id"], "initial", runtime.episode)
        runtime.decide(pending["plan_id"], True)
        runtime.standing_policy["contact_hold"] = grant
        runtime.start()
        runtime.targets[0]["pose"] = [600, 400, 0]
        runtime.targets[0]["speed"] = 0
        for now in (1, 2, 3):
            runtime.sim_time = now
            runtime._observe()
        contact = next(iter(runtime.contacts.values()))
        assert contact["state"] == "confirmed"
        assert runtime.active["UUV-1"]["phase"] == ("provisional" if grant else "scanning")
        assert len(runtime.regions) == (7 if grant else 8)
        if grant:
            assert runtime.standing_policy["enabled"]
            assert runtime.active["UUV-1"]["acquisition_mode"] == "active"
            assert runtime.active["UUV-1"]["contact_id"] == contact["contact_id"]
            assert runtime.active["UUV-1"]["expires_at_s"] > runtime.sim_time
            assert any(e["type"] == "provisional_contact_started" and e["data"]["observation_id"] for e in runtime.events)
            assert runtime.metrics["effective_tracking_seconds"] == 0
            before = [col[:] for col in runtime.scan_times]
            pose = runtime.uuvs[0]["pose"]
            first_footprint = [(col, row) for col in range(len(before)) for row in range(len(before[col]))
                if side_scan_contains(pose, [(col+.5)*runtime.config.cell,
                    runtime.config.height-(row+.5)*runtime.config.cell], runtime.config)]
            runtime.sim_time = 4
            runtime._observe()
            assert not sensor_roles(runtime.active["UUV-1"])["side_scan"]
            assert all(runtime.scan_times[col][row] == before[col][row] for col, row in first_footprint), "Holding contact is not area scanning"
            proposal = runtime.calculate("plan_tracking", {"contact_id": contact["contact_id"]})
            assert proposal["status"] == "succeeded", proposal
            assert "UUV-1" in proposal["members"], "The safe discoverer should be preferred for the permanent pair"
            approval = runtime.submit(proposal["result_id"], "team", runtime.episode)
            assert approval["status"] == "pending_approval"
            runtime.tick()
            assert runtime.status == "running"
            assert runtime.active["UUV-1"]["phase"] == "provisional"
            assert sum(a["kind"] == "track" for a in runtime.active.values()) == 1
    finally:
        runtime.close()


def test_provisional_contact_lease_expires_into_authorized_gap_repair(tmp_path):
    runtime = MissionRuntime(tmp_path/"hold-expiry.sqlite")
    try:
        runtime.submit(runtime.calculate("plan_search", {"standing_policy": True})["result_id"], "start", runtime.episode)
        runtime.start()
        runtime.targets[0]["pose"] = [600, 400, 0]
        runtime.targets[0]["speed"] = 0
        for now in (1, 2, 3):
            runtime.sim_time = now
            runtime._observe()
        assert runtime.active["UUV-1"]["phase"] == "provisional"
        runtime.contacts[next(iter(runtime.contacts))]["state"] = "lost"
        runtime.tick()
        assert runtime.active["UUV-1"]["phase"] == "reacquiring"
        runtime.sim_time = runtime.active["UUV-1"]["expires_at_s"]
        runtime.tick()
        assert runtime.status == "running"
        assert runtime.active["UUV-1"]["kind"] == "search"
        assert len(runtime.regions) == 8
        assert any(event["type"] == "provisional_contact_expired" for event in runtime.events)
    finally:
        runtime.close()


def test_infeasible_provisional_control_restores_authorized_search(tmp_path, monkeypatch):
    runtime = MissionRuntime(tmp_path/"hold-unsafe.sqlite")
    try:
        runtime.submit(runtime.calculate("plan_search", {"standing_policy": True})["result_id"], "start", runtime.episode)
        runtime.start()
        runtime.targets[0]["pose"] = [600, 400, 0]
        runtime.targets[0]["speed"] = 0
        for now in (1, 2, 3):
            runtime.sim_time = now
            runtime._observe()
        assert runtime.active["UUV-1"]["phase"] == "provisional"
        original = runtime_module.choose_controls
        attempts = []

        def fail_first(*args, **kwargs):
            attempts.append(1)
            return None if len(attempts) == 1 else original(*args, **kwargs)

        monkeypatch.setattr(runtime_module, "choose_controls", fail_first)
        runtime.tick()
        assert len(attempts) == 2
        assert runtime.status == "running"
        assert runtime.active["UUV-1"]["kind"] == "search"
        assert len(runtime.regions) == 8
        assert any(event["type"] == "provisional_contact_aborted" for event in runtime.events)
    finally:
        runtime.close()


@pytest.mark.parametrize("size", [2, 3])
@pytest.mark.parametrize("phase", ["acquiring", "reacquiring"])
def test_active_bridge_requires_measured_geometry_then_fresh_passive_streak(size, phase):
    boats = [{"id": f"UUV-{i+1}", "pose": pose, "generation": 0, "capabilities": ["active", "passive"]}
        for i, pose in enumerate(([1800, 2000, 0], [2000, 1800, math.pi/2], [2200, 2000, math.pi])[:size])]
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
    assert all(action.get("established_once") for action in runtime.active.values())
    assert all(sample["mode"] == "active" for sample in runtime.observations if sample["time_s"] == 2)
    assert all(time < 0 for column in runtime.scan_times for time in column), "Forward acquisition is not side-scan coverage"
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
