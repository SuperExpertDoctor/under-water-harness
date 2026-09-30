import copy
import pytest

from uuv_game.runtime import MissionRuntime, MissionError


@pytest.fixture
def runtime(tmp_path):
    instance = MissionRuntime(tmp_path / "resilience.sqlite")
    yield instance
    instance.close()


def candidate(runtime, member="UUV-1"):
    return runtime.calculate("plan_search", {"members": [member], "bbox": [300, 300, 1700, 1700]})


def test_automatic_backpressure_never_interrupts_tick(runtime):
    for i in range(12):
        runtime.queue_agent(str(i))
    runtime.start()
    runtime.sim_time = 30
    runtime.tick()
    assert runtime.status == "running"
    assert runtime.sim_time > 30
    with pytest.raises(MissionError, match="agent_queue_full"):
        runtime.queue_agent("human overflow")


def test_disjoint_approvals_survive_candidate_eviction(runtime):
    runtime.set_mode("request")
    first = candidate(runtime)
    second = runtime.calculate("plan_search", {"members": ["UUV-2"], "bbox": [300, 1800, 1700, 3200]})
    a = runtime.submit(first["result_id"], "a", runtime.episode)
    b = runtime.submit(second["result_id"], "b", runtime.episode)
    runtime.results.clear()
    runtime.decide(a["plan_id"], True)
    runtime.decide(b["plan_id"], True)
    assert set(runtime.active) == {"UUV-1", "UUV-2"}


def test_replacing_one_member_does_not_invent_motion_for_others(runtime):
    runtime.set_mode("full")
    result = runtime.calculate("plan_search", {"members": ["UUV-1", "UUV-2"], "bbox": [300, 300, 2100, 1700]})
    original = runtime.submit(result["result_id"], "team", runtime.episode)
    replacement = candidate(runtime)
    runtime.submit(replacement["result_id"], "solo", runtime.episode)
    assert runtime.active["UUV-2"]["kind"] == "search"
    assert runtime.active["UUV-2"]["plan_id"] == original["plan_id"]
    region = next(region for region in runtime.frame()["search_regions"] if region["assigned_uav_id"] == "UUV-2")
    assert region["assigned_uav_id"] == "UUV-2"


def test_closed_route_repeats_without_unapproved_loiter(runtime):
    runtime.set_mode("full")
    result = candidate(runtime)
    plan = runtime.submit(result["result_id"], "repeat", runtime.episode)
    assert plan["execution_domain"]
    assert plan["fallback"] == "repeat_approved_closed_route"
    action = runtime.active["UUV-1"]
    runtime.uuvs[0]["pose"] = list(action["points"][-1])
    action["index"] = len(action["points"])-2
    runtime.start()
    runtime.tick()
    assert action["kind"] == "search"
    assert action["points"]
    assert action["index"] == action.get("cycle_start_index", 0)


def test_target_truth_is_used_only_for_independent_collision_pause(runtime):
    original = list(runtime.uuvs[0]["pose"])
    runtime.targets[0]["pose"] = list(original)
    runtime.start()
    runtime.tick()
    # The interlock holds the colliding boat for a tick while the quarry keeps
    # maneuvering, instead of deadlocking the sim on identical geometry.
    assert runtime.status == "running"
    assert runtime.uuvs[0]["pose"] == original
    assert any(event["type"] == "target_collision_hold" for event in runtime.events)
    assert "targets" not in runtime.mission_state()


def test_plan_history_is_bounded_but_receipts_survive_restart(runtime, tmp_path):
    runtime.set_mode("full")
    result = candidate(runtime)
    first = runtime.submit(result["result_id"], "original", runtime.episode)
    for i in range(120):
        item = copy.deepcopy(runtime.plans[first["plan_id"]])
        item.update(plan_id=f"old-{i}", status="superseded")
        runtime.plans[item["plan_id"]] = item
    runtime.save()
    assert len(runtime.plans) <= 40
    assert "receipts" not in runtime.store.load()
    assert runtime.store.get_receipt("original")["response"] == first


def test_search_planner_avoids_unassigned_stationary_fleet(runtime):
    from uuv_game.algorithms.planning import path_safe
    result = candidate(runtime)
    points = runtime.results[result["result_id"]]["routes"]["UUV-1"]
    stationary = [{"x": u["pose"][0], "y": u["pose"][1], "radius": 44} for u in runtime.uuvs[1:]]
    assert path_safe(points, stationary, (0, 0, 4000, 4000))


def test_pause_cannot_clear_stop_latch(runtime):
    runtime.stop()
    with pytest.raises(MissionError, match="mission_stopped"):
        runtime.pause()
    with pytest.raises(MissionError, match="reset_required_after_stop"):
        runtime.start()
    assert runtime.status == "stopped"
