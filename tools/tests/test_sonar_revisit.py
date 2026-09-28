"""Coverage is produced by an observed side-scan swath, not sonar mode labels."""

import math

from uuv_game.config import Config
from uuv_game.runtime import MissionRuntime
from uuv_game import sensing
from uuv_game.algorithms.partition import partition_regions


def test_bilateral_side_scan_rotates_with_vehicle_heading():
    config = Config()
    assert sensing.side_scan_contains([2000, 2000, 0], [2000, 2200], config)
    assert sensing.side_scan_contains([2000, 2000, 0], [2000, 1800], config)
    assert not sensing.side_scan_contains([2000, 2000, 0], [2200, 2000], config)
    assert not sensing.side_scan_contains([2000, 2000, 0], [2000, 2020], config)
    assert sensing.side_scan_contains([2000, 2000, math.pi / 2], [2200, 2000], config)


def test_search_scans_sides_but_not_forward_cells(tmp_path):
    game = MissionRuntime(tmp_path / "side.sqlite")
    try:
        game.uuvs[0]["pose"] = [2050, 2050, 0]
        game.active["UUV-1"] = {"kind": "search", "phase": "scanning"}
        game.sim_time = 1
        game._observe()
        assert game.scan_times[20][17] == 1
        assert game.scan_times[22][19] < 0
        assert sensing.sensor_roles(game.active["UUV-1"])["side_scan"]
    finally:
        game.close()


def test_forward_acquisition_does_not_refresh_search_history(tmp_path):
    game = MissionRuntime(tmp_path / "forward.sqlite")
    try:
        game.uuvs[0]["pose"] = [2050, 2050, 0]
        game.targets[0]["pose"] = [2250, 2050, 0]
        game.active["UUV-1"] = {"kind": "track", "phase": "acquiring", "acquisition_mode": "active", "contact_id": "C-1"}
        game.sim_time = 1
        game._observe()
        assert game.observations[-1]["mode"] == "active"
        assert all(time < 0 for column in game.scan_times for time in column)
        assert sensing.sensor_roles(game.active["UUV-1"])["forward_active"]
        assert not sensing.sensor_roles(game.active["UUV-1"])["side_scan"]
    finally:
        game.close()


def test_passive_tracking_keeps_front_obstacle_sonar_without_search_coverage(tmp_path):
    game = MissionRuntime(tmp_path / "passive.sqlite")
    try:
        game.uuvs[0]["pose"] = [2050, 2050, 0]
        game.active["UUV-1"] = {"kind": "track", "phase": "tracking", "acquisition_mode": "passive", "contact_id": "C-1"}
        game.sim_time = 1
        game._observe()
        roles = sensing.sensor_roles(game.active["UUV-1"])
        assert roles == {"side_scan": False, "forward_active": True, "forward_passive": True}
        assert all(time < 0 for column in game.scan_times for time in column)
    finally:
        game.close()


def test_configured_rolling_window_expires_without_erasing_cumulative_scan(tmp_path):
    game = MissionRuntime(tmp_path / "window.sqlite", Config(coverage_window_min=2))
    try:
        game.scan_times[0][0] = 1
        game.scan_times[0][1] = 1
        game.sim_time = 120
        frame = game.frame()
        assert frame["coverage_metrics"]["primary_window_min"] == 2
        assert frame["coverage_metrics"]["cumulative_pct"] > 0
        game.sim_time = 122
        stale = game.frame()
        assert stale["coverage_metrics"]["cumulative_pct"] == frame["coverage_metrics"]["cumulative_pct"]
        assert stale["coverage_metrics"]["primary_coverage_pct"] == 0
        assert stale["mission_metrics"]["recent_coverage_pct"] == 0
    finally:
        game.close()


def test_revisit_partition_marks_only_expired_or_never_scanned_cells_pending():
    ledger = [[80.0] * 40 for _ in range(40)]
    ledger[5][5] = 99.0
    ledger[6][6] = -1.0
    boat = {"id": "UUV-1", "pose": [500, 500, 0], "remaining_range_m": 100000, "capabilities": ["active"]}
    region = partition_regions([boat], ledger, [], now=100.0, window_s=10.0)["regions"][0]
    pending = set(map(tuple, region["scan_cells"]))
    assert (0, 0) in pending
    assert (6, 6) in pending
    assert (5, 5) not in pending


def test_coverage_gap_triggers_only_when_free_scanner_lacks_an_owned_region(tmp_path):
    game = MissionRuntime(tmp_path / "gaps.sqlite")
    try:
        assert game._search_gap()
        result = game.calculate("plan_search", {"standing_policy": True})
        game.submit(result["result_id"], "all-scanners", game.episode)
        assert not game._search_gap()
        game.active.pop("UUV-1")
        assert game._search_gap()
    finally:
        game.close()


def test_partial_patrol_replan_requirement_is_preserved_on_activation(tmp_path):
    game = MissionRuntime(tmp_path / "partial-plan.sqlite")
    try:
        result = game.calculate("plan_search", {"standing_policy": True})
        game.results[result["result_id"]]["partial_patrols"] = {"UUV-1": {"partial_patrol": True}}
        game.submit(result["result_id"], "partial-scanner", game.episode)
        assert game.active["UUV-1"]["requires_replan_after_cycle"] is True
        assert all(not game.active[boat["id"]].get("requires_replan_after_cycle") for boat in game.uuvs[1:])
    finally:
        game.close()
