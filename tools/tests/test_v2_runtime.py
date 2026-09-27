import copy
import json
import math

import pytest

from uuv_game.runtime import MissionRuntime, MissionError


@pytest.fixture
def runtime(tmp_path):
    runtime = MissionRuntime(tmp_path / "v2.sqlite")
    yield runtime
    runtime.close()


def test_own_telemetry_contains_energy_generation_and_sensor_mode(runtime):
    boat = runtime.frame()["uavs"][0]
    assert boat["generation"] == 1
    assert boat["remaining_range_m"] > 0
    assert boat["energy_pct"] == 100
    assert boat["sensor_mode"] == "off"


def test_observation_queries_and_duplicate_cycles_do_not_resample(runtime):
    runtime.targets[0]["pose"] = [600, 400, 0]
    runtime.active["UUV-1"] = {"kind": "search"}
    runtime._observe()
    before = copy.deepcopy(runtime.observations)
    runtime._observe()
    runtime.mission_state()
    assert runtime.observations == before
    assert "TARGET-1" not in json.dumps(runtime.mission_state())


def test_passive_only_does_not_update_active_coverage(runtime):
    runtime.targets[0]["pose"] = [600, 400, 0]
    runtime.active["UUV-1"] = {"kind": "search"}
    runtime._observe()
    contact = next(iter(runtime.contacts))
    runtime.active["UUV-1"] = {"kind": "track", "phase": "acquiring", "contact_id": contact}
    before = copy.deepcopy(runtime.scan_times)
    runtime.sim_time = 1
    runtime._observe()
    sample = runtime.observations[-1]
    assert sample["mode"] == "passive"
    assert not ({"x", "y", "range_m"} & sample.keys())
    assert runtime.scan_times == before
    assert runtime.contacts[contact]["state"] != "tracking"


def test_generation_change_invalidates_candidate(runtime):
    result = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    runtime.uuvs[0]["generation"] += 1
    assert "generation_changed" in runtime.evaluate(result["result_id"])["errors"]


def test_fleet_search_creates_eight_unique_regions_only_after_approval(runtime):
    runtime.set_mode("request")
    result = runtime.calculate("plan_search", {"standing_policy": True})
    assert result["status"] == "succeeded", result
    assert not runtime.active
    pending = runtime.submit(result["result_id"], "fleet", runtime.episode)
    assert pending["status"] == "pending_approval"
    runtime.decide(pending["plan_id"], True)
    regions = runtime.frame()["search_regions"]
    assert len(regions) == 8
    assert len({region["assigned_uav_id"] for region in regions}) == 8
    assert runtime.standing_policy["enabled"]


def test_scene_truth_absent_from_normal_frame(runtime):
    runtime.vessels.append({"scenario_entity_id": "hidden-vessel", "position": [10, 10]})
    assert runtime.frame()["scenario_vessels"] == []
    assert "hidden-vessel" not in json.dumps(runtime.mission_state())


def test_feedback_is_not_coalesced_with_periodic_review(runtime):
    periodic = runtime.queue_agent("review", "periodic")
    feedback = runtime.queue_agent("keep this instruction", "feedback")
    assert feedback["run_id"] != periodic["run_id"]
    assert feedback["text"] == "keep this instruction"


def test_entry_transit_need_not_be_closed_but_patrol_must_be(runtime):
    result = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    stored = runtime.results[result["result_id"]]
    points = stored["routes"]["UUV-1"]
    stored["routes"]["UUV-1"] = [[350, 400, 0]] + points
    stored["cycle_start_indices"] = {"UUV-1": 1}
    assert "closed_continuation_required" not in runtime.evaluate(result["result_id"])["errors"]


def test_candidate_cannot_activate_after_energy_depletion(runtime):
    result = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    runtime.uuvs[0]["remaining_range_m"] = 20
    assert "insufficient_energy" in runtime.evaluate(result["result_id"])["errors"]


def test_replay_keeps_historical_conversation_not_future_text(runtime):
    runtime.messages = [{"id": "m1", "role": "assistant", "text": "historical", "status": "completed"}]
    runtime.store.frame(runtime.episode, runtime.frame())
    runtime.messages[0]["text"] = "future"
    replay = runtime.store.replay(runtime.episode, 0, 1)
    assert replay["frames"][0]["messages"][0]["text"] == "historical"


def test_explicit_region_already_owned_cannot_create_empty_responsibility(runtime):
    runtime.set_mode("full")
    first = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    runtime.submit(first["result_id"], "owned", runtime.episode)
    second = runtime.calculate("plan_search", {"members": ["UUV-2"], "bbox": [300, 300, 1700, 1700]})
    assert second["status"] == "infeasible"
    assert not runtime.evaluate(second["result_id"])["valid"]


def test_contact_allocation_is_a_cooperative_candidate_not_a_search_partition(runtime):
    runtime.contacts["CONTACT-1"] = {"x": 1000, "y": 1000, "vx": 1, "vy": 0, "state": "confirmed", "uncertainty_m": 10}
    result = runtime.calculate("compute_task_allocation", {"contact_id": "CONTACT-1"})
    assert result["status"] == "succeeded", result
    assert 2 <= len(result["teams"][0]["members"]) <= 3
    assert result["teams"][0]["task_id"] == "CONTACT-1"
    assert not runtime.active
    assert "not_executable" in runtime.evaluate(result["result_id"])["errors"]


def test_bounded_search_without_explicit_members_cannot_expand_to_whole_map(runtime):
    with pytest.raises(MissionError, match="bbox_requires_explicit_members") as error:
        runtime.calculate("plan_search", {"bbox": [300, 300, 1700, 1700]})
    assert error.value.status == 422
    assert not runtime.results
    assert not runtime.active
    assert not runtime.standing_policy["enabled"]
