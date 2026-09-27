import json
import math

import pytest

from uuv_game.runtime import MissionRuntime, MissionError


@pytest.fixture
def runtime(tmp_path):
    instance = MissionRuntime(tmp_path / "mission.sqlite")
    yield instance
    instance.close()


def test_initial_observations_do_not_reveal_truth(runtime):
    state = runtime.mission_state()
    assert len(state["uuvs"]) == 8
    assert state["contacts"] == []
    assert "targets" not in state
    assert "scenario_vessels" not in state
    assert len(runtime.frame()["uavs"]) == 8


def test_simulation_advances_without_model_or_approval(runtime):
    runtime.start()
    before = runtime.sim_time
    for _ in range(10):
        runtime.tick()
    assert runtime.sim_time > before
    assert runtime.agent["status"] != "running"


def test_candidate_never_executes_and_request_needs_approval(runtime):
    runtime.set_mode("request")
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    assert candidate["status"] == "succeeded"
    assert not runtime.active
    result = runtime.submit(candidate["result_id"], "test-command", runtime.episode)
    assert result["status"] == "pending_approval"
    runtime.start()
    runtime.tick()
    assert runtime.sim_time > 0
    assert not runtime.active
    approved = runtime.decide(result["plan_id"], True)
    assert approved["status"] == "active"
    assert "UUV-1" in runtime.active


def test_idempotency_stale_episode_and_stop(runtime):
    runtime.set_mode("full")
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    first = runtime.submit(candidate["result_id"], "same", runtime.episode)
    assert runtime.submit(candidate["result_id"], "same", runtime.episode) == first
    with pytest.raises(MissionError):
        runtime.submit(candidate["result_id"], "wrong", "old-episode")
    runtime.stop()
    before = runtime.sim_time
    runtime.tick()
    assert runtime.sim_time == before
    assert not runtime.active
    with pytest.raises(MissionError):
        runtime.submit(candidate["result_id"], "after-stop", runtime.episode)


def test_invalid_members_rejected(runtime):
    for members in [["UUV-1"] * 2, ["UUV-1", "UUV-2", "UUV-3", "UUV-4"], ["unknown"]]:
        with pytest.raises(MissionError):
            runtime.calculate("plan_search", {"members": members})


def test_persistence_of_pending_plan(tmp_path):
    path = tmp_path / "state.sqlite"
    first = MissionRuntime(path)
    first.set_mode("request")
    result = first.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    plan = first.submit(result["result_id"], "persist", first.episode)
    first.close()
    second = MissionRuntime(path)
    assert second.plans[plan["plan_id"]]["status"] == "pending_approval"
    assert second.submit(result["result_id"], "persist", second.episode) == plan
    second.close()


def test_reset_invalidates_results(runtime):
    result = runtime.calculate("plan_search", {"members": ["UUV-1"]})
    old = runtime.episode
    runtime.reset()
    assert runtime.episode != old
    with pytest.raises(MissionError):
        runtime.submit(result["result_id"], "old", old)


def test_motion_is_bounded_and_coverage_updates(runtime):
    runtime.set_mode("full")
    result = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    runtime.submit(result["result_id"], "run", runtime.episode)
    runtime.start()
    before = list(runtime.uuvs[0]["pose"])
    for _ in range(50):
        runtime.tick()
    after = runtime.uuvs[0]["pose"]
    assert 0 < math.dist(before[:2], after[:2]) <= 40.01
    assert max(max(column) for column in runtime.frame()["info_matrix"]) > 0
    assert "api_key" not in json.dumps(runtime.frame())
