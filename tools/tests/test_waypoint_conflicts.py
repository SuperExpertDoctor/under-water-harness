"""A* route primitives and bounded conflict-based multi-UUV waypoints."""

import math
import importlib
import pytest

from uuv_game.algorithms import planning
from uuv_game.algorithms.tracking import tracking_plan
from uuv_game.algorithms.coverage import plan_search
from uuv_game.mission_planning import search_bundle

def collision_solver():
    return importlib.import_module("uuv_game.algorithms.conflicts")


def test_obstacle_forces_pose_lattice_astar_without_breaking_curvature():
    start, goal = [500, 1000, 0], [1500, 1000, 0]
    result = planning.plan_path(start, goal, obstacles=[{"x": 1000, "y": 1000, "radius": 110}], budget=1800)
    assert result["status"] == "succeeded", result["diagnostics"]
    assert result["diagnostics"]["expanded"] > 0
    assert planning.path_safe(result["points"], [{"x": 1000, "y": 1000, "radius": 110}], (0, 0, 4000, 4000))
    for left, right in zip(result["points"], result["points"][1:]):
        length = math.dist(left[:2], right[:2])
        assert abs(math.remainder(right[2]-left[2], math.tau)) <= length / 60 * 1.01 + 1e-8


def test_cbs_detects_continuous_time_crossing_and_returns_safe_waypoints():
    conflicts = collision_solver()
    starts = {"U1": [400, 1000, 0], "U2": [1000, 400, math.pi / 2]}
    goals = {"U1": [1600, 1000, 0], "U2": [1000, 1600, math.pi / 2]}
    routes = {name: planning.plan_path(start, goals[name])["points"] for name, start in starts.items()}
    assert conflicts.first_conflict(routes, separation=40, horizon_s=180) is not None
    solution = conflicts.resolve_conflicts(starts, goals, routes, [], budget=24, horizon_s=180)
    assert solution["status"] == "succeeded", solution
    assert solution["diagnostics"]["expanded"] > 0
    assert conflicts.first_conflict(solution["routes"], separation=40, horizon_s=180) is None
    assert all(planning.path_safe(path, [], (0, 0, 4000, 4000)) for path in solution["routes"].values())


def test_cbs_budget_exhaustion_never_returns_unsafe_success():
    conflicts = collision_solver()
    starts = {"U1": [400, 1000, 0], "U2": [1000, 400, math.pi / 2]}
    goals = {"U1": [1600, 1000, 0], "U2": [1000, 1600, math.pi / 2]}
    routes = {name: planning.plan_path(start, goals[name])["points"] for name, start in starts.items()}
    solution = conflicts.resolve_conflicts(starts, goals, routes, [], budget=0, horizon_s=180)
    assert solution["status"] == "infeasible"
    assert solution["routes"] == {}


def test_tracking_team_transits_are_jointly_conflict_checked():
    fleet = [{"id": f"U{i}", "pose": [400 + 550*i, 400, math.pi/2],
              "remaining_range_m": 30000, "capabilities": ["active", "passive"]} for i in range(5)]
    candidate = tracking_plan(fleet, {"x": 1800, "y": 1600, "vx": .2, "vy": .1, "uncertainty_m": 20}, [])
    assert candidate["status"] == "succeeded", candidate
    assert candidate["diagnostics"]["conflict_solver"] == "bounded-pose-cbs-v1"
    assert collision_solver().first_conflict(candidate["routes"], horizon_s=180) is None


def test_search_bundle_checks_joint_routes_without_losing_closed_patrol():
    fleet = [{"id": f"U{i}", "pose": [400 + 550*i, 400, math.pi/2],
              "remaining_range_m": 50000, "capabilities": ["active", "passive"]} for i in range(3)]
    result = search_bundle(fleet, [[-1.0] * 40 for _ in range(40)], [])
    assert result["status"] == "succeeded", result
    assert result["diagnostics"]["conflict_solver"] == "bounded-pose-cbs-v1"
    assert all(points[-1] == pytest.approx(points[result["cycle_start_indices"][name]]) for name, points in result["routes"].items())


def test_explicit_search_routes_also_pass_through_joint_conflict_solver():
    fleet = [{"id": "U1", "pose": [350, 350, 0]}, {"id": "U2", "pose": [3650, 350, math.pi]}]
    result = plan_search(fleet, [250, 250, 3750, 3750])
    assert result["status"] == "succeeded", result
    assert result["diagnostics"]["conflict_solver"] == "bounded-pose-cbs-v1"
    assert collision_solver().first_conflict(result["routes"], horizon_s=180) is None
