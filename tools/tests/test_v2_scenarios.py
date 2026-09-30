"""Offline v2 scenarios; no paid models, fabricated contacts or boat teleporting."""

import importlib
import json

import pytest

from uuv_game.runtime import MissionError, MissionRuntime


def acceptance():
    spec = importlib.util.find_spec("acceptance.v2_acceptance")
    assert spec is not None, "v2 acceptance driver must exist"
    return importlib.import_module(spec.name)


def test_short_probe_reports_its_scope_without_claiming_long_acceptance(tmp_path):
    report = acceptance().run_acceptance(sim_seconds=5, output=tmp_path/"probe.json")
    assert report["run_completed"], report
    assert report["sim_seconds"] == pytest.approx(5)
    assert report["fleet_size_min"] == report["fleet_size_max"] == 8
    assert report["search_region_counts"] == [[8, 8]]
    assert not report["passed"]
    assert report["model_connected"] is False
    assert not report["failures"]
    assert (tmp_path/"probe.json").exists()


def test_wall_probe_advances_during_requested_interval(tmp_path):
    report = acceptance().run_acceptance(sim_seconds=2, wall_seconds=2, output=tmp_path/"paced.json")
    assert report["run_completed"], report
    assert report["wall_seconds"] >= 2
    assert report["invariant_ticks"] == 10
    assert report["samples"][0]["sim_s"] == pytest.approx(0.2)
    assert report["samples"][0]["wall_s"] < report["wall_seconds"]
    assert not report["passed"]


def test_observed_contact_transit_and_effective_cooperative_tracking(tmp_path):
    report = acceptance().run_acceptance(sim_seconds=1200, output=tmp_path/"tracking.json")
    assert report["run_completed"], report["failures"]
    assert report["confirmed_contact_time_s"] is not None
    assert report["first_tracking_time_s"] > report["confirmed_contact_time_s"]
    assert "transit" in report["tracking_phases"]
    assert "tracking" in report["tracking_phases"]
    assert report["metrics"]["effective_tracking_seconds"] >= 30
    assert [8, 8] in report["search_region_counts"]
    assert [6, 6] in report["search_region_counts"]
    assert report["fleet_size_min"] == report["fleet_size_max"] == 8
    assert report["min_tracking_geometry"] >= 0.3
    assert report["max_tracking_uncertainty_m"] <= 120


def test_request_permissions_preserve_fleet_until_explicit_approval(tmp_path):
    runtime = MissionRuntime(tmp_path/"request.sqlite")
    try:
        runtime.set_mode("request")
        candidate = runtime.calculate("plan_search", {"standing_policy": True})
        assert candidate["status"] == "succeeded", candidate
        receipt = runtime.submit(candidate["result_id"], "request-fleet", runtime.episode)
        assert receipt["status"] == "pending_approval"
        assert runtime.active == {}
        runtime.decide(receipt["plan_id"], True)
        assert len(runtime.regions) == len(runtime.active) == 8
        assert runtime.standing_policy["enabled"]
        assert runtime.submit(candidate["result_id"], "request-fleet", runtime.episode) == receipt
    finally:
        runtime.close()


def test_generation_replacement_rejects_old_pending_approval(tmp_path):
    runtime = MissionRuntime(tmp_path/"generation.sqlite")
    try:
        runtime.set_mode("request")
        candidate = runtime.calculate("plan_search", {"standing_policy": True})
        receipt = runtime.submit(candidate["result_id"], "pending-generation", runtime.episode)
        # Fault injection only: emulate a delayed command for a replaced entity.
        runtime.uuvs[0]["generation"] += 1
        with pytest.raises(MissionError, match="generation_changed"):
            runtime.decide(receipt["plan_id"], True)
        assert not runtime.active
    finally:
        runtime.close()


def test_low_energy_fault_exits_and_replaces_without_losing_ownership(tmp_path):
    runtime = MissionRuntime(tmp_path/"energy-fault.sqlite")
    try:
        runtime.set_mode("full")
        candidate = runtime.calculate("plan_search", {"standing_policy": True})
        assert candidate["status"] == "succeeded", candidate
        runtime.submit(candidate["result_id"], "energy-fault-start", runtime.episode)
        runtime.targets = []
        runtime.start()
        # Deliberate energy fault, not natural-endurance acceptance evidence.
        runtime.uuvs[0]["remaining_range_m"] = 3000
        saw_exit = False
        for _ in range(4000):
            before = {u["id"]: {"generation": u["generation"], "pose": u["pose"][:], "remaining_range_m": u["remaining_range_m"]} for u in runtime.uuvs}
            runtime.tick()
            assert runtime.status == "running", json.dumps(runtime.events[-5:])
            assert acceptance()._audit(runtime, before) == []
            saw_exit |= runtime.active.get("UUV-1", {}).get("kind") == "exit"
            if runtime.uuvs[0]["generation"] == 2:
                break
        assert saw_exit
        assert runtime.uuvs[0]["generation"] == 2
        assert runtime.metrics["rotation_count"] == 1
        for _ in range(300):
            if "UUV-1" in runtime.active and len(runtime.regions) == 8:
                break
            runtime.tick()
        assert len(runtime.uuvs) == len(runtime.regions) == 8
        entry = next(event["data"] for event in runtime.events if event["type"] == "uuv_replenished")
        assert entry["exit_point"] == entry["entry_point"]
        x, y = entry["entry_point"]
        assert min(x, y, runtime.config.width-x, runtime.config.height-y) == pytest.approx(0)
    finally:
        runtime.close()
