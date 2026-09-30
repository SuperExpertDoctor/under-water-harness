"""Deterministic numerical contracts for the mission algorithm core."""

import importlib
import math

import pytest


@pytest.fixture
def algorithms():
    try:
        return importlib.import_module("uuv_game.algorithms")
    except ModuleNotFoundError:
        pytest.skip("algorithm core is not implemented yet")


def test_algorithm_core_exists():
    try:
        core = importlib.util.find_spec("uuv_game.algorithms")
    except ModuleNotFoundError:
        core = None
    assert core is not None, "algorithm core must be implemented"


def test_exact_straight_and_arc(algorithms):
    assert algorithms.integrate([10, 20, 0], 4, 0, 2) == [18, 20, 0]
    assert algorithms.integrate([0, 0, 0], 4, 1 / 60, 15 * math.pi / 2) == pytest.approx([60, 60, math.pi / 2])


@pytest.mark.parametrize("pose,speed,curvature,dt", [([0, 0, math.nan], 4, 0, 1), ([0, 0], 4, 0, 1), ([0, 0, 0], 0, 0, 1), ([0, 0, 0], 4, math.inf, 1), ([0, 0, 0], 4, 0, -1)])
def test_integrate_validation(algorithms, pose, speed, curvature, dt):
    with pytest.raises(ValueError):
        algorithms.integrate(pose, speed, curvature, dt)


def test_dubins_six_families_and_endpoints(algorithms):
    kernel = importlib.import_module("uuv_game.vendor.dubins")
    families = kernel.candidates([500, 500, 0], [590, 560, 1.2], 60)
    assert {"".join(mode) for mode, lengths in families} == {"LSL", "RSR", "LSR", "RSL", "RLR", "LRL"}
    for modes, lengths in families:
        pose = [500, 500, 0]
        for mode, length in zip(modes, lengths):
            pose = algorithms.integrate(pose, 1, {"L": 1 / 60, "R": -1 / 60, "S": 0}[mode], length)
        assert pose == pytest.approx([590, 560, 1.2], abs=1e-7)
    path = algorithms.plan_path([500, 500, 0], [1700, 900, 1.2])
    assert path["status"] == "succeeded"
    assert path["points"][0] == pytest.approx([500, 500, 0])
    assert path["points"][-1] == pytest.approx([1700, 900, 1.2])
    assert algorithms.path_safe(path["points"], [], (0, 0, 4000, 4000))


@pytest.mark.parametrize("radius", [0, -1, math.nan, math.inf])
def test_path_radius_validation(algorithms, radius):
    with pytest.raises(ValueError):
        algorithms.plan_path([100, 100, 0], [200, 200, 0], radius=radius)


def test_obstacle_detour(algorithms):
    obstacles = [{"x": 1100, "y": 1000, "radius": 180}]
    result = algorithms.plan_path([500, 1000, 0], [1700, 1000, 0], obstacles=obstacles)
    assert result["status"] == "succeeded", result
    assert result["length_m"] > 1200
    assert algorithms.path_safe(result["points"], obstacles, (0, 0, 4000, 4000))
    for left, right in zip(result["points"], result["points"][1:]):
        distance = math.dist(left[:2], right[:2])
        angle = abs(math.remainder(right[2] - left[2], 2 * math.pi))
        assert distance <= 5.001
        assert angle <= distance / 60 * 1.001 + 1e-8


def test_swept_collision_detects_thin_obstacle(algorithms):
    assert not algorithms.path_safe([[100, 100, 0], [200, 100, 0]], [{"x": 151, "y": 100, "radius": 0.1}], (0, 0, 400, 400), margin=0)
    assert not algorithms.path_safe([[1, 10, 0]], [], (0, 0, 400, 400))
    assert not algorithms.path_safe([], [], (0, 0, 400, 400))


def test_infeasible_and_budget_are_explicit(algorithms):
    blocked = algorithms.plan_path([500, 1000, 0], [1700, 1000, 0], obstacles=[{"x": 500, "y": 1000, "radius": 20}])
    assert blocked["status"] == "infeasible" and not blocked["points"]
    exhausted = algorithms.plan_path([500, 1000, 0], [1700, 1000, 0], obstacles=[{"x": 1100, "y": 1000, "radius": 180}], budget=1)
    assert exhausted["status"] == "timed_out" and not exhausted["points"]


def test_allocation_unique_and_maximum_three(algorithms):
    uuvs = [{"id": str(i), "pose": [100 * i, 100, 0]} for i in range(8)]
    tasks = [{"id": "a", "center": [400, 400], "size": 3, "priority": 10}, {"id": "b", "center": [800, 400], "size": 3, "priority": 5}, {"id": "c", "center": [100, 200], "size": 2, "priority": 1}]
    result = algorithms.allocate_tasks(uuvs, tasks)
    members = [member for team in result["teams"] for member in team["members"]]
    assert result["status"] == "succeeded"
    assert len(members) == len(set(members)) == 8
    assert all(1 <= len(team["members"]) <= 3 for team in result["teams"])
    assert algorithms.allocate_tasks(uuvs[:2], tasks)["status"] == "infeasible"
    with pytest.raises(ValueError):
        algorithms.allocate_tasks(uuvs + [uuvs[0]], tasks)


def test_search_continuous_closed_routes(algorithms):
    uuvs = [{"id": "a", "pose": [300, 300, 0]}, {"id": "b", "pose": [1700, 300, 0]}]
    result = algorithms.plan_search(uuvs, [200, 200, 2200, 1800])
    assert result["status"] == "succeeded", result
    for uuv in uuvs:
        points = result["routes"][uuv["id"]]
        assert points[0] == pytest.approx(uuv["pose"])
        assert points[-1] == pytest.approx(points[result["cycle_start_indices"][uuv["id"]]])
        assert algorithms.path_safe(points, [], (0, 0, 4000, 4000))
        for left, right in zip(points, points[1:]):
            assert math.dist(left[:2], right[:2]) <= 5.001
            assert abs(math.remainder(right[2] - left[2], 2 * math.pi)) <= 5.001 / 60


@pytest.mark.parametrize("velocity", [0, 0.5])
def test_tracking_stationary_slow_target_orbits(algorithms, velocity):
    pose = [1180, 1000, math.pi / 2]
    for tick in range(4000):
        estimate = {"x": 1000 + velocity * tick * 0.2, "y": 1000, "vx": velocity, "vy": 0}
        curvature = algorithms.tracking_control(pose, estimate)
        assert math.isfinite(curvature) and abs(curvature) <= 1 / 60
        pose = algorithms.integrate(pose, 4, curvature, 0.2)
    distance = math.dist(pose[:2], [estimate["x"], estimate["y"]])
    assert 120 < distance < 260


def test_follow_path_bounded_and_validation(algorithms):
    assert algorithms.follow_path([100, 100, 0], [[100, 100, 0], [200, 100, 0]]) == pytest.approx(0)
    assert abs(algorithms.follow_path([100, 100, math.pi], [[100, 100, 0], [200, 100, 0]])) <= 1 / 60
    with pytest.raises(ValueError):
        algorithms.follow_path([100, 100, 0], [])


def test_follow_path_turns_when_facing_away(algorithms):
    command = algorithms.follow_path([100, 100, math.pi], [[100, 100, 0], [200, 100, 0]])
    assert abs(command) == pytest.approx(1 / 60)


def test_arc_sagitta_collision_margin(algorithms):
    points = [[160, 100, math.pi / 2], [100, 160, math.pi]]
    obstacle = {"x": 100 + 60 / math.sqrt(2), "y": 100 + 60 / math.sqrt(2), "radius": 0.1}
    assert not algorithms.path_safe(points, [obstacle], (0, 0, 400, 400), margin=0)


def test_eight_vehicle_search_default_map(algorithms):
    uuvs = [{"id": f"uuv-{index}", "pose": [250 + 500 * index, 300, math.pi / 2]} for index in range(8)]
    result = algorithms.plan_search(uuvs, [0, 0, 4000, 4000])
    assert result["status"] == "succeeded", result["diagnostics"]
    assert len(result["routes"]) == 8


@pytest.mark.parametrize("call", [
    lambda a: a.plan_path([100, 100, 0], [200, 200, 0], obstacles=[{"x": 150, "y": 150, "radius": -1}]),
    lambda a: a.path_safe([[100, 100, 0]], [], [0, 0, 0, 0]),
    lambda a: a.tracking_control([100, 100, 0], {"x": 100, "y": 100, "vx": math.nan, "vy": 0}),
    lambda a: a.allocate_tasks([{"id": "a"}], []),
    lambda a: a.allocate_tasks([{"id": "a", "pose": [100, 100, 0]}], [{"id": "t"}]),
    lambda a: a.path_safe(None, [], [0, 0, 4000, 4000]),
    lambda a: a.plan_search([{"id": "a"}], [0, 0, 4000, 4000]),
    lambda a: a.plan_search([{"id": []}], [0, 0, 4000, 4000]),
    lambda a: a.plan_path([100, 100, 0], [200, 200, 0], radius=100000, step=0.1),
])
def test_malformed_inputs_rejected(algorithms, call):
    with pytest.raises(ValueError):
        call(algorithms)
