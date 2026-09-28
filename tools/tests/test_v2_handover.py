"""Offline handover contracts; synthetic measurements are not acceptance evidence."""

import copy
import importlib
import math

import pytest

from uuv_game.algorithms.planning import path_safe
from uuv_game.runtime import MissionRuntime


@pytest.fixture
def runtime(tmp_path):
    runtime = MissionRuntime(tmp_path / "handover.sqlite")
    runtime.obstacles = []
    # Isolate handover geometry from the randomized deployment perimeter.
    for index, boat in enumerate(runtime.uuvs):
        boat["pose"] = [400 if index < 4 else 3000, 400 + (index % 4) * 1000, 0]
    runtime.set_mode("full")
    fleet = runtime.calculate("plan_search", {"standing_policy": True})
    assert fleet["status"] == "succeeded", fleet
    runtime.submit(fleet["result_id"], "fleet-fixture", runtime.episode)
    runtime.contacts["CONTACT-1"] = {"contact_id": "CONTACT-1", "x": 1100, "y": 1100,
        "vx": 0, "vy": 0, "uncertainty_m": 30, "state": "confirmed", "last_seen": 0, "samples": []}
    tracking = runtime.calculate("plan_tracking", {"members": ["UUV-1", "UUV-2"], "contact_id": "CONTACT-1"})
    assert tracking["status"] == "succeeded", tracking
    runtime.submit(tracking["result_id"], "tracking-fixture", runtime.episode)
    for member in ("UUV-1", "UUV-2"):
        runtime.active[member]["phase"] = "tracking"
    runtime.uuvs[0]["remaining_range_m"] = 5000
    yield runtime
    runtime.close()


def handover_module():
    spec = importlib.util.find_spec("uuv_game.handover")
    assert spec is not None, "bounded standing-policy handover module must exist"
    return importlib.import_module(spec.name)


def test_preparation_uses_real_routes_and_atomic_search_repair(runtime):
    module = handover_module()
    original = copy.deepcopy(runtime.active)
    original_plans = copy.deepcopy(runtime.plans)
    scan = copy.deepcopy(runtime.scan_times)
    assert module.prepare_handover(runtime)
    incoming = runtime.active["UUV-1"]["relief_member"]
    assert original[incoming]["kind"] == "search"
    assert runtime.active[incoming]["kind"] == "track"
    assert runtime.active[incoming]["phase"] == "transit"
    boat = next(boat for boat in runtime.uuvs if boat["id"] == incoming)
    assert runtime.active[incoming]["points"][0] == pytest.approx(boat["pose"])
    assert path_safe(runtime.active[incoming]["points"], [], [0, 0, 4000, 4000])
    assert runtime.active["UUV-2"] == original["UUV-2"]
    assert runtime.active["UUV-1"]["points"] == original["UUV-1"]["points"]
    assert len([action for action in runtime.active.values() if action.get("contact_id") == "CONTACT-1" and action["kind"] == "track"]) == 3
    assert len(runtime.regions) == 5
    assert incoming not in {region["owner"] for region in runtime.regions}
    assert runtime.scan_times == scan
    plan_id = runtime.active[incoming]["plan_id"]
    assert runtime.plans[plan_id]["members"] == original_plans[plan_id]["members"]
    assert runtime.plans[plan_id]["generations"] == original_plans[plan_id]["generations"]
    assert runtime.plans[plan_id]["active_generations"][incoming] == boat["generation"]
    assert runtime.metrics["handoff_count"] == 0
    assert not module.prepare_handover(runtime)


@pytest.mark.parametrize("grant", ["enabled", "energy_rotation", "local_repair"])
def test_handover_requires_all_standing_authorizations(runtime, grant):
    module = handover_module()
    runtime.standing_policy[grant] = False
    before = copy.deepcopy((runtime.active, runtime.regions, runtime.plans))
    assert not module.prepare_handover(runtime)
    assert (runtime.active, runtime.regions, runtime.plans) == before


@pytest.mark.parametrize("failure", ["tracking", "repair"])
def test_failed_planning_does_not_reassign_any_boat(runtime, monkeypatch, failure):
    module = handover_module()
    monkeypatch.setattr(module, "tracking_plan" if failure == "tracking" else "search_bundle",
        lambda *args, **kwargs: {"status": "infeasible", "diagnostics": {"reason": "injected"}})
    before = copy.deepcopy((runtime.active, runtime.regions, runtime.plans, runtime.revision, runtime.metrics))
    assert not module.prepare_handover(runtime)
    assert (runtime.active, runtime.regions, runtime.plans, runtime.revision, runtime.metrics) == before


def samples(runtime, incoming, now, *, mode="passive", generation=None, angle=math.pi/2):
    """Explicit synthetic unit observations, independent of simulator truth."""
    runtime.sim_time = now
    for member, bearing in ((incoming, 0), ("UUV-2", angle)):
        boat = next(boat for boat in runtime.uuvs if boat["id"] == member)
        runtime.observations.append({"sample_id": f"{member}:{now}", "time_s": now, "contact_id": "CONTACT-1",
            "observer_id": member, "generation": boat["generation"] if generation is None else generation,
            "mode": mode, "bearing_rad": bearing, "observer_pose": list(boat["pose"])})


def test_handoff_completes_only_after_sustained_replacement_observations(runtime):
    module = handover_module()
    assert module.prepare_handover(runtime)
    incoming = runtime.active["UUV-1"]["relief_member"]
    assert not module.finish_handover(runtime)
    runtime.active[incoming]["phase"] = "acquiring"
    for now in (1, 2, 3):
        samples(runtime, incoming, now)
        assert not module.finish_handover(runtime)
        assert runtime.active["UUV-1"]["kind"] == "track"
        assert not module.finish_handover(runtime), "re-reading the same samples must not extend the streak"
    runtime.uuvs[0]["pose"] = [800, 1000, math.pi]
    runtime.uuvs[0]["remaining_range_m"] = 1201
    samples(runtime, incoming, 4)
    assert not module.finish_handover(runtime), "handover must wait for the nearest-boundary range threshold"
    runtime.uuvs[0]["remaining_range_m"] = 1200
    samples(runtime, incoming, 5)
    assert module.finish_handover(runtime)
    assert runtime.active["UUV-1"]["kind"] == "exit"
    assert runtime.active["UUV-1"]["exit_point"] == [0, 1000]
    assert path_safe(runtime.active["UUV-1"]["points"], [], [-100, -100, 4100, 4100])
    assert runtime.metrics["handoff_count"] == 1
    assert len([a for a in runtime.active.values() if a["kind"] == "track"]) == 2
    assert not module.finish_handover(runtime)
    assert runtime.metrics["handoff_count"] == 1


@pytest.mark.parametrize("reason", ["transit", "active", "generation", "geometry", "uncertainty", "missing", "survivor_energy"])
def test_invalid_acquisition_cannot_count_handoff(runtime, reason):
    module = handover_module()
    assert module.prepare_handover(runtime)
    incoming = runtime.active["UUV-1"]["relief_member"]
    runtime.active[incoming]["phase"] = "transit" if reason == "transit" else "acquiring"
    if reason == "uncertainty":
        runtime.contacts["CONTACT-1"]["uncertainty_m"] = 121
    if reason == "survivor_energy":
        runtime.uuvs[1]["remaining_range_m"] = 100
    for now in range(1, 6):
        samples(runtime, incoming, now, mode="active" if reason == "active" else "passive",
            generation=0 if reason == "generation" else None, angle=.1 if reason == "geometry" else math.pi/2)
        if reason == "missing":
            runtime.observations = [sample for sample in runtime.observations if sample["observer_id"] != incoming]
        assert not module.finish_handover(runtime)
    assert runtime.metrics["handoff_count"] == 0
    assert runtime.active["UUV-1"]["kind"] == "track"


def test_three_boat_team_never_adds_a_fourth(runtime):
    module = handover_module()
    runtime.active["UUV-3"] = {**copy.deepcopy(runtime.active["UUV-2"]), "slot": 2}
    before = copy.deepcopy(runtime.active)
    assert not module.prepare_handover(runtime)
    assert runtime.active == before


def test_equal_age_fleet_can_handover_when_actual_routes_leave_safe_reserves(runtime):
    module = handover_module()
    for boat in runtime.uuvs:
        boat["remaining_range_m"] = 5500
    # Synthetic prior coverage models the work done before an equal-age fleet reaches reserve.
    runtime.scan_times = [[0.0]*40 for _ in range(40)]
    assert module.prepare_handover(runtime)
    incoming = runtime.active["UUV-1"]["relief_member"]
    assert runtime.active[incoming]["phase"] == "transit"
    assert runtime.metrics["handoff_count"] == 0


def test_stale_samples_and_unavailable_exit_do_not_complete_handoff(runtime, monkeypatch):
    module = handover_module()
    assert module.prepare_handover(runtime)
    incoming = runtime.active["UUV-1"]["relief_member"]
    runtime.active[incoming]["phase"] = "acquiring"
    for now in (1, 2, 3):
        samples(runtime, incoming, now)
        assert not module.finish_handover(runtime)
    runtime.sim_time = 5
    assert not module.finish_handover(runtime)
    assert runtime.active["UUV-1"]["relief_streak_s"] == 0
    monkeypatch.setattr(module, "exit_route", lambda *_: None)
    for now in (6, 7, 8, 9, 10):
        samples(runtime, incoming, now)
        assert not module.finish_handover(runtime)
    assert runtime.active["UUV-1"]["kind"] == "track"
    assert runtime.metrics["handoff_count"] == 0
