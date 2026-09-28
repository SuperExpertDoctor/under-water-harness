import json
import math

import pytest

from uuv_game.runtime import MissionRuntime, MissionError
from uuv_game.lifecycle import exit_route, replacement_pose, prepare_exits
from uuv_game.algorithms.control import TIMES
from uuv_game.algorithms.motion import integrate
from uuv_game.algorithms.planning import path_safe


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


def test_all_initial_boats_start_at_random_boundary_points(runtime, tmp_path):
    width, height = runtime.config.width, runtime.config.height
    for boat in runtime.uuvs:
        x, y, heading = boat["pose"]
        assert min(x, y, width - x, height - y) == pytest.approx(0)
        assert heading == pytest.approx(0 if x == 0 else math.pi if x == width else math.pi / 2 if y == 0 else -math.pi / 2)
    assert len({tuple(boat["pose"][:2]) for boat in runtime.uuvs}) == 8
    other = MissionRuntime(tmp_path / "seeded.sqlite")
    try:
        assert [boat["pose"] for boat in runtime.uuvs] == [boat["pose"] for boat in other.uuvs]
    finally:
        other.close()


def test_turnover_exits_to_nearest_boundary_and_reenters_at_that_point(runtime, monkeypatch):
    boat = runtime.uuvs[0]
    boat["pose"] = [800, 1000, math.pi]
    runtime.obstacles = []
    route = exit_route(runtime, boat)
    assert route is not None
    assert route["points"][-1][:2] == pytest.approx([-30, 1000])
    runtime.standing_policy["energy_rotation"] = True
    runtime.standing_policy["plan_id"] = "authorized-rotation"
    runtime.active[boat["id"]] = {"kind": "search", "generation": 1}
    monkeypatch.setattr("uuv_game.lifecycle.repair_search", lambda *_args, **_kwargs: True)
    boat["remaining_range_m"] = 1201
    assert prepare_exits(runtime)
    assert runtime.active[boat["id"]]["kind"] == "search"
    boat["remaining_range_m"] = 1200
    assert prepare_exits(runtime)
    assert runtime.active[boat["id"]]["exit_point"] == [0, 1000]
    positions = {other["id"]: [1800 + index * 200, 2000, 0] for index, other in enumerate(runtime.uuvs)}
    positions[boat["id"]] = route["points"][-1]
    replacement = replacement_pose(runtime, boat, positions)
    assert replacement is not None
    assert replacement == pytest.approx([0, 1000, 0])


def test_exit_crossing_replaces_boat_on_same_boundary_in_one_tick(runtime, monkeypatch):
    boat = runtime.uuvs[0]
    boat["pose"] = [1, 2000, math.pi]
    boat["remaining_range_m"] = 1000
    runtime.standing_policy.update(energy_rotation=True, local_repair=True, plan_id="authorized-rotation")
    runtime.active[boat["id"]] = {"kind": "exit", "phase": "exiting", "plan_id": "authorized-rotation",
        "generation": 1, "exit_point": [0, 2000], "points": [[1, 2000, math.pi], [-30, 2000, math.pi]],
        "index": 0, "execution_domain": [-100, -100, 4100, 4100]}
    monkeypatch.setattr("uuv_game.lifecycle.repair_search", lambda *_args, **_kwargs: True)
    points = [integrate(boat["pose"], 4, 0, t) for t in TIMES]
    assert path_safe(points, runtime.obstacles, [-100, -100, 4100, 4100], margin=10), points
    runtime.start()
    runtime.tick()
    assert boat["generation"] == 1
    runtime.tick()
    assert runtime.status == "running", [event["type"] for event in runtime.events[-5:]]
    assert boat["generation"] == 2
    assert boat["pose"] == [0, 2000, 0]
    assert runtime.metrics["rotation_count"] == 1
    assert [event["data"]["entry_point"] for event in runtime.events if event["type"] == "uuv_replenished"] == [[0, 2000]]


def test_replacement_uses_actual_crossing_not_original_exit_projection(runtime):
    boat = runtime.uuvs[0]
    boat["pose"] = [2, 1995, math.pi]
    runtime.active[boat["id"]] = {"kind": "exit", "exit_point": [0, 1995]}
    next_poses = {other["id"]: other["pose"] for other in runtime.uuvs}
    next_poses[boat["id"]] = [-2, 2005, math.pi]
    assert replacement_pose(runtime, boat, next_poses) == [0, 2000, 0]


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
    for members in [["UUV-1"] * 2, [f"UUV-{i}" for i in range(1, 10)], ["unknown"]]:
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
