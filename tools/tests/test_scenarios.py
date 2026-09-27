"""Deterministic game scenarios, deliberately independent of a paid model."""
import math
import pytest

from uuv_game.runtime import MissionRuntime
from uuv_game.config import Config
from uuv_game.observations import initialize
from uuv_game.sensing import sensor_mode


def deploy_fleet(runtime):
    result = runtime.calculate("plan_search", {"standing_policy": True})
    assert result["status"] == "succeeded", result
    plan = runtime.submit(result["result_id"], "fleet", runtime.episode)
    assert plan["status"] == "active"
    runtime.start()


def test_eight_search_detect_track_lost_reacquire(tmp_path):
    runtime = MissionRuntime(tmp_path/"scenario.sqlite")
    try:
        deploy_fleet(runtime)
        for _ in range(9000):
            runtime.tick()
            assert runtime.status == "running"
            if any(c["state"] == "confirmed" for c in runtime.contacts.values()):
                break
        contact = next(c for c in runtime.contacts.values() if c["state"] == "confirmed")
        candidate = runtime.calculate("plan_tracking", {"contact_id": contact["contact_id"]})
        assert candidate["status"] == "succeeded", candidate
        pending = runtime.submit(candidate["result_id"], "track", runtime.episode)
        assert pending["status"] == "pending_approval"
        runtime.decide(pending["plan_id"], True)
        before = runtime.sim_time
        for _ in range(3000):
            runtime.tick()
            assert runtime.status == "running"
        assert runtime.sim_time == before+600
        assert contact["state"] == "tracking"
        assert len(runtime.active) == 8
        assert sum(action["kind"] == "search" for action in runtime.active.values()) == 6
        runtime.sensor_enabled = False
        for _ in range(110):
            runtime.tick()
            assert runtime.status == "running"
        assert contact["state"] == "lost"
        assert all(action["phase"] == "reacquiring" for action in runtime.active.values() if action["kind"] == "track")
        effective_before = runtime.metrics["effective_tracking_seconds"]
        for _ in range(5):
            runtime.tick()
        assert runtime.metrics["effective_tracking_seconds"] == effective_before
        runtime.sensor_enabled = True
        restored_at = runtime.sim_time
        # This seed needs 34 seconds with the explicit active acquisition bridge;
        # 45 seconds bounds this scenario, not arbitrary target maneuvers.
        for _ in range(225):
            runtime.tick()
            assert runtime.status == "running"
            if any(sensor_mode(action) == "active" for action in runtime.active.values() if action["kind"] == "track"):
                assert runtime.metrics["effective_tracking_seconds"] == effective_before
            if contact["state"] == "tracking":
                break
            assert runtime.metrics["effective_tracking_seconds"] == effective_before
        assert contact["state"] == "tracking", {"contact": contact, "uuvs": runtime.uuvs, "targets": runtime.targets}
        assert runtime.sim_time-restored_at <= 45
        assert any(event["type"] == "tracking_passive_acquisition_started" and event["time"]*60 > restored_at
            for event in runtime.events)
        members = {member for member, action in runtime.active.items() if action["kind"] == "track"}
        assert all(sensor_mode(runtime.active[member]) == "passive" for member in members)
        for observed_at in (runtime.sim_time, runtime.sim_time-1, runtime.sim_time-2):
            received = {sample["observer_id"] for sample in runtime.observations
                if sample["time_s"] == observed_at and sample["mode"] == "passive" and sample["contact_id"] == contact["contact_id"]}
            assert members <= received
        assert contact["tracking_streak"] >= 3
        assert contact["geometry_quality"] >= .3
        assert contact["uncertainty_m"] <= 120
        assert runtime.metrics["effective_tracking_seconds"] > effective_before
    finally:
        runtime.close()


def test_lost_belief_reacquires_from_active_measurement_not_hidden_truth(tmp_path):
    runtime = MissionRuntime(tmp_path/"reacquisition.sqlite")
    try:
        runtime.targets[0]["pose"] = [600, 400, 0]
        runtime.sim_time = 30
        contact = {**initialize(2000, 2000, 30, .1), "contact_id": "CONTACT-1", "last_seen": 0,
            "state": "lost", "hits": [], "samples": [], "tracking_streak": 0, "observers": []}
        runtime.contacts["CONTACT-1"] = contact
        runtime.contact_mapping["TARGET-1"] = "CONTACT-1"
        runtime.active["UUV-1"] = {"kind": "track", "phase": "reacquiring", "contact_id": "CONTACT-1", "generation": 1}
        runtime._observe()
        assert contact["last_seen"] == 30
        assert contact["state"] == "degraded", "one active fix must not count as cooperative passive tracking"
        assert list(runtime.contacts) == ["CONTACT-1"]
        sample = runtime.observations[-1]
        assert sample["mode"] == "active"
        assert contact["x"] == pytest.approx(sample["observer_pose"][0]+sample["range_m"]*math.cos(sample["bearing_rad"]))
        assert contact["y"] == pytest.approx(sample["observer_pose"][1]+sample["range_m"]*math.sin(sample["bearing_rad"]))
        assert [contact["x"], contact["y"]] != runtime.targets[0]["pose"][:2]
        assert any(event["type"] == "contact_reacquired" for event in runtime.events)
        assert runtime.metrics["effective_tracking_seconds"] == 0
    finally:
        runtime.close()


@pytest.mark.parametrize("state,phase", [("lost", "acquiring"), ("tracking", "reacquiring")])
def test_filter_reset_requires_lost_contact_and_active_measurement(tmp_path, state, phase):
    runtime = MissionRuntime(tmp_path/"no-reinitialization.sqlite")
    try:
        runtime.targets[0]["pose"] = [600, 400, 0]
        runtime.sim_time = 30
        contact = {**initialize(2000, 2000, 30, .1), "contact_id": "CONTACT-1", "last_seen": 0,
            "state": state, "hits": [], "samples": [], "tracking_streak": 0, "observers": []}
        runtime.contacts["CONTACT-1"] = contact
        runtime.contact_mapping["TARGET-1"] = "CONTACT-1"
        runtime.active["UUV-1"] = {"kind": "track", "phase": phase, "contact_id": "CONTACT-1", "generation": 1}
        runtime._observe()
        assert contact["last_seen"] == 0
        assert [contact["x"], contact["y"]] == [2000, 2000]
        assert not any(event["type"] == "contact_reacquired" for event in runtime.events)
        assert all(not ({"x", "y", "range_m"} & sample.keys()) for sample in runtime.observations if sample["mode"] == "passive")
    finally:
        runtime.close()


def test_new_contact_id_does_not_reuse_deleted_public_identifier(tmp_path):
    runtime = MissionRuntime(tmp_path/"contact-ids.sqlite")
    try:
        runtime.targets = [{"id": name, "pose": [600, y, 0], "speed": 0} for name, y in (("first", 400), ("second", 450))]
        runtime.active["UUV-1"] = {"kind": "search"}
        runtime._observe()
        first = runtime.contact_mapping.pop("first")
        second = runtime.contact_mapping["second"]
        runtime.contacts.pop(first)
        runtime.targets = [target for target in runtime.targets if target["id"] != "first"]
        runtime.targets.append({"id": "third", "pose": [600, 500, 0], "speed": 0})
        runtime.sim_time = 1
        runtime._observe()
        third = runtime.contact_mapping["third"]
        assert third not in (first, second)
        assert set(runtime.contacts) == {second, third}
    finally:
        runtime.close()


def test_four_hours_approved_search_without_model(tmp_path):
    # Isolate closed-route continuation. Natural endurance/turnover is checked by
    # the separate eight-boat v2 acceptance, not this legacy one-boat scenario.
    runtime = MissionRuntime(tmp_path/"four-hours.sqlite", Config(range_capacity=100000))
    try:
        runtime.targets = []
        result = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
        runtime.submit(result["result_id"], "long-search", runtime.episode)
        runtime.start()
        for _ in range(72000):
            runtime.tick()
            assert runtime.status == "running"
            assert abs(runtime.uuvs[0]["curvature"]) <= 1/60
        assert runtime.sim_time == 14400
        assert sum(e["type"] == "search_complete" for e in runtime.events) >= 7
        assert len(runtime.agent_jobs) == 1  # Coalesced, no worker consuming.
    finally:
        runtime.close()
