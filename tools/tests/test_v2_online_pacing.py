"""Simulation pacing leaves a bounded real-model round-trip budget, without relaxing route freshness."""

import pytest

from uuv_game.config import Config
from uuv_game.runtime import MissionError, MissionRuntime


@pytest.mark.parametrize("multiplier, expected_valid", [(None, True), (10.0, False)])
def test_online_round_trip_budget_keeps_route_freshness_enforced(tmp_path, multiplier, expected_valid):
    config = Config() if multiplier is None else Config(simulation_speed=multiplier)
    runtime = MissionRuntime(tmp_path/"pacing.sqlite", config)
    try:
        runtime.set_mode("full")
        fleet = runtime.calculate("plan_search", {"standing_policy": True})
        runtime.submit(fleet["result_id"], "initial-fleet", runtime.episode)
        candidate = runtime.calculate("plan_search", {"standing_policy": True})
        runtime.start()
        simulated_budget = 20*config.simulation_speed
        while runtime.sim_time < simulated_budget:
            runtime.tick()
            assert runtime.status == "running"
        assessment = runtime.evaluate(candidate["result_id"])
        assert assessment["valid"] is expected_valid, assessment
        if expected_valid:
            assert runtime.submit(candidate["result_id"], "after-model-round-trips", runtime.episode)["status"] == "active"
        else:
            assert "start_pose_changed_replan" in assessment["errors"]
            with pytest.raises(MissionError, match="start_pose_changed_replan"):
                runtime.submit(candidate["result_id"], "stale-fast-forward", runtime.episode)
    finally:
        runtime.close()
