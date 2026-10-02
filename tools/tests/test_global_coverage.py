"""Automatic coverage owns all searchable cells without stealing other tasks."""

import copy

import pytest

from uuv_game.algorithms.partition import _components
from uuv_game.capabilities.lifecycle import repair_search
from uuv_game.runtime import MissionRuntime


@pytest.fixture
def game(tmp_path):
    runtime = MissionRuntime(tmp_path / "global-coverage.sqlite")
    runtime.set_mode("full")
    yield runtime
    runtime.close()


def assert_full_partition(regions, owners):
    assert {region["owner"] for region in regions} == set(owners)
    assert len(regions) == len(owners)
    cells = [tuple(cell) for region in regions for cell in region["cells"]]
    assert len(cells) == len(set(cells)) == 1584
    for region in regions:
        assert len(_components(set(map(tuple, region["cells"])))) == 1


def test_initial_global_search_and_role_switch_repair_preserve_coverage(game):
    initial = game.calculate("plan_search", {"standing_policy": True})
    assert initial["status"] == "succeeded", initial
    game.submit(initial["result_id"], "initial-search", game.episode)
    assert_full_partition(game.regions, [boat["id"] for boat in game.uuvs])
    game.scan_times[0][0] = 10
    game.scan_times[30][30] = 20
    scans = copy.deepcopy(game.scan_times)
    game.active["UUV-1"] = {"kind": "track", "phase": "tracking", "plan_id": "tracking-existing"}
    game.active["UUV-2"] = {"kind": "track", "phase": "transit", "plan_id": "tracking-existing"}
    game.active["UUV-3"] = {"kind": "exit", "phase": "exiting", "plan_id": "exit-existing"}
    protected = copy.deepcopy({key: game.active[key] for key in ("UUV-1", "UUV-2", "UUV-3")})
    result = game.calculate("plan_search", {"standing_policy": True})
    assert result["status"] == "succeeded", result
    game.submit(result["result_id"], "repair-search", game.episode)
    assert_full_partition(game.regions, [f"UUV-{index}" for index in range(4, 9)])
    assert all(game.active[key] == action for key, action in protected.items())
    assert game.scan_times == scans


@pytest.mark.parametrize("unavailable", ["passive_only", "low_energy", "path"])
@pytest.mark.parametrize("tool", ["partition_search_area", "compute_task_allocation", "plan_search"])
def test_automatic_partition_excludes_unavailable_boats_without_area_holes(game, unavailable, tool):
    boat = game.uuvs[0]
    if unavailable == "passive_only":
        boat["capabilities"] = ["passive"]
    elif unavailable == "low_energy":
        boat["remaining_range_m"] = 100
    else:
        game.active[boat["id"]] = {"kind": "path", "phase": "transit", "plan_id": "path-existing"}
    result = game.calculate(tool, {})
    assert result["members"] == [f"UUV-{index}" for index in range(2, 9)]
    assert result["status"] == "succeeded", result
    assert_full_partition(game.results[result["result_id"]]["regions"], result["members"])


@pytest.mark.parametrize("unavailable", ["exit", "passive_only", "low_energy"])
def test_no_coverage_boats_reports_infeasible_not_success_with_unowned_area(game, unavailable):
    if unavailable == "exit":
        game.active = {boat["id"]: {"kind": "exit", "plan_id": "exit"} for boat in game.uuvs}
    elif unavailable == "passive_only":
        for boat in game.uuvs:
            boat["capabilities"] = ["passive"]
    else:
        for boat in game.uuvs:
            boat["remaining_range_m"] = 100
    result = game.calculate("plan_search", {})
    assert result["status"] == "infeasible"
    assert result["diagnostics"]["reason"] == "no_eligible_coverage_vehicles"
    assert result["diagnostics"]["unowned_cells"] == 1584
    assert result["regions"] == []
    assert not game.evaluate(result["result_id"])["valid"]


@pytest.mark.parametrize("kind", ["search", "reacquire"])
def test_coverage_task_transit_keeps_its_region(game, kind):
    game.active["UUV-1"] = {"kind": kind, "phase": "transit", "plan_id": "coverage-transit"}
    result = game.calculate("partition_search_area", {})
    assert result["status"] == "succeeded"
    assert_full_partition(game.results[result["result_id"]]["regions"], [boat["id"] for boat in game.uuvs])


def test_last_search_boat_can_relinquish_coverage_for_authorized_exit(game):
    game.standing_policy.update(local_repair=True, energy_rotation=True, plan_id="search-policy")
    game.active["UUV-1"] = {"kind": "search", "plan_id": "search-policy"}
    assert repair_search(game, exclude=["UUV-1"])
    assert game.regions == []
    assert game.status != "safety_paused"
