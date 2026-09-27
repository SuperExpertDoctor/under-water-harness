"""Joint forward-only control selection, independent of the runtime."""

import copy
import importlib
import math

from uuv_game.algorithms.motion import integrate


def choose():
    spec = importlib.util.find_spec("uuv_game.algorithms.control")
    assert spec is not None, "joint controller must exist"
    return importlib.import_module(spec.name).choose_controls


def requests(boats, preferred=0):
    return {boat["id"]: {"preferred": preferred, "execution_domain": [0, 0, 4000, 4000]} for boat in boats}


def test_joint_selector_fast_path_is_pure_and_keeps_preferred():
    boats = [{"id": "a", "pose": [500, 500, 0], "curvature": 0}, {"id": "b", "pose": [1000, 1000, 0], "curvature": 0}]
    before = copy.deepcopy(boats)
    result = choose()(boats, requests(boats), [], [])
    assert result == {"a": 0, "b": 0}
    assert boats == before


def test_joint_selector_resolves_recorded_head_on_conflict():
    boats = [
        {"id": "UUV-3", "pose": [1492.923478, 3365.440278, 1.4641286], "curvature": -0.00192475},
        {"id": "UUV-5", "pose": [1528.676983, 3466.363572, -2.00889646], "curvature": 0.000871203},
    ]
    commands = requests(boats)
    commands["UUV-3"]["preferred"] = -0.00192475
    commands["UUV-5"]["preferred"] = 0.000871203
    result = choose()(boats, commands, [], [])
    assert result is not None
    assert set(result) == {"UUV-3", "UUV-5"}
    for tick in range(181):
        predicted = [integrate(boat["pose"], 4, result[boat["id"]], tick/10) for boat in boats]
        assert math.dist(predicted[0][:2], predicted[1][:2]) >= 44


def test_joint_selector_returns_none_for_blocked_pose_and_respects_idle_peer():
    boats = [{"id": "a", "pose": [500, 500, 0]}, {"id": "idle", "pose": [520, 500, 0]}]
    assert choose()(boats, requests(boats[:1]), [], []) is None
    assert choose()(boats[:1], requests(boats[:1]), [], [{"x": 500, "y": 500, "radius": 20}]) is None


def test_joint_selector_explains_blocked_candidates():
    boats = [{"id": "a", "pose": [500, 500, 0]}]
    diagnostics = {}
    assert choose()(boats, requests(boats), [], [{"x": 500, "y": 500, "radius": 20}], diagnostics=diagnostics) is None
    assert diagnostics["reason"] == "no_static_safe_candidates"
    assert diagnostics["blocked_boat"] == "a"
    assert diagnostics["candidate_counts"]["a"] == 0


def test_observation_clearance_buffer_turns_before_hard_envelope():
    boats = [{"id": "a", "pose": [950, 1050, 0], "curvature": 0}]
    contacts = [{"x": 1000, "y": 1000, "vx": 0, "vy": 0, "uncertainty_m": 2}]
    result = choose()(boats, requests(boats), contacts, [])
    assert result is not None
    assert result["a"] > 0
    for tick in range(181):
        pose = integrate(boats[0]["pose"], 4, result["a"], tick/10)
        assert math.dist(pose[:2], [1000, 1000]) >= 42


def test_high_uncertainty_requests_earlier_avoidance_without_weakening_envelope():
    boats = [{"id": "a", "pose": [950, 1140, 0], "curvature": 0}]
    contacts = [{"x": 1000, "y": 1000, "vx": 0, "vy": 0, "uncertainty_m": 140}]
    result = choose()(boats, requests(boats), contacts, [])
    assert result is not None
    assert result["a"] > 0
    for tick in range(181):
        pose = integrate(boats[0]["pose"], 4, result["a"], tick/10)
        assert math.dist(pose[:2], [1000, 1000]) >= 100


def test_unrelated_boats_do_not_prune_feasible_conflict_resolution():
    boats = [
        {"id": "UUV-1", "pose": [1625.148061, 3715.281815, -2.44092591], "curvature": 0.000260432},
        {"id": "UUV-2", "pose": [850, 812.219703, -math.pi/2], "curvature": 0},
        {"id": "UUV-3", "pose": [1489.431089, 3347.332163, 1.53293705], "curvature": 1/90},
        {"id": "UUV-4", "pose": [750, 2776.275586, -math.pi/2], "curvature": 0},
        {"id": "UUV-5", "pose": [1537.046548, 3482.761205, -1.97396088], "curvature": -0.00002674665},
        {"id": "UUV-6", "pose": [2245.099372, 1448.953802, -1.65031768], "curvature": 0.012},
        {"id": "UUV-7", "pose": [2887.199146, 3214.717410, 0.62402305], "curvature": 0},
        {"id": "UUV-8", "pose": [3750, 808.995174, -math.pi/2], "curvature": 0},
    ]
    preferred = [0.0001178528, -5.399e-12, -0.0059246105, 1.977e-13, -0.0004754839, 0.010353522, 5.491e-12, -3.827e-11]
    commands = requests(boats)
    for boat, curvature in zip(boats, preferred):
        commands[boat["id"]]["preferred"] = curvature
    contacts = [{"x": 1700.592514, "y": 3471.468220, "vx": -2.012295, "vy": -1.423722, "uncertainty_m": 3.67404}]
    diagnostics = {}
    result = choose()(boats, commands, contacts, [{"x": 2500, "y": 2300, "radius": 140}], diagnostics=diagnostics)
    assert result is not None, diagnostics


def test_crossing_pair_continues_with_bounded_curvature_and_separation():
    boats = [{"id": "a", "pose": [1500, 2000, 0], "curvature": 0},
             {"id": "b", "pose": [2000, 1500, math.pi/2], "curvature": 0}]
    planner = choose()
    for tick in range(1500):
        result = planner(boats, requests(boats), [], [])
        assert result is not None, (tick, boats)
        previous = [boat["pose"][:] for boat in boats]
        for boat in boats:
            curvature = result[boat["id"]]
            assert abs(curvature) <= 1/60
            boat["pose"] = integrate(boat["pose"], 4, curvature, 0.2)
            boat["curvature"] = curvature
        assert math.dist(boats[0]["pose"][:2], boats[1]["pose"][:2]) >= 44
        assert all(math.dist(old[:2], boat["pose"][:2]) <= 0.800001 for old, boat in zip(previous, boats))
