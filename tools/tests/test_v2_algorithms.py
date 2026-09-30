"""Pure v2 allocation, coverage and cooperative transit contracts."""

import copy
import importlib
import math

import pytest

from uuv_game.algorithms.planning import path_safe
from uuv_game.algorithms.motion import integrate


def fleet(count=8):
    return [{"id": f"uuv-{i}", "pose": [250 + 500 * i, 300, math.pi / 2],
             "generation": 1, "remaining_range_m": 50000,
             "capabilities": ["active", "passive"]} for i in range(count)]


def kernel(module, name):
    spec = importlib.util.find_spec(f"uuv_game.algorithms.{module}")
    assert spec is not None, f"{module} kernel must exist"
    function = getattr(importlib.import_module(spec.name), name, None)
    assert callable(function), f"{name} must exist"
    return function


def connected(cells):
    remaining = set(map(tuple, cells))
    if not remaining:
        return False
    pending = [remaining.pop()]
    while pending:
        x, y = pending.pop()
        for neighbor in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if neighbor in remaining:
                remaining.remove(neighbor)
                pending.append(neighbor)
    return not remaining


def test_partition_complete_connected_and_pure():
    scan = [[-1.0] * 40 for _ in range(40)]
    scan[2][5] = 123.0
    before = copy.deepcopy(scan)
    boats = fleet()
    result = kernel("partition", "partition_regions")(boats, scan, [])
    assert result["status"] == "succeeded", result
    assert len(result["regions"]) == 8
    assert {r["owner"] for r in result["regions"]} == {b["id"] for b in boats}
    cells = [tuple(c) for r in result["regions"] for c in r["cells"]]
    assert len(cells) == len(set(cells)) == 1600
    assert all(connected(r["cells"]) for r in result["regions"])
    assert all(len(r["bbox_m"]) == 4 for r in result["regions"])
    assert scan == before


def test_partition_local_merge_split_and_zero():
    partition = kernel("partition", "partition_regions")
    scan = [[-1.0] * 40 for _ in range(40)]
    boats = fleet()
    initial = partition(boats, scan, [])
    reduced = partition(boats[1:], scan, [], initial)
    assert reduced["status"] == "succeeded"
    assert len(reduced["regions"]) == 7
    old = {r["owner"]: r["cells"] for r in initial["regions"]}
    assert sum(r["cells"] == old[r["owner"]] for r in reduced["regions"]) >= 6
    restored = partition(boats, scan, [], reduced)
    assert len(restored["regions"]) == 8
    assert all(connected(r["cells"]) for r in restored["regions"])
    assert partition([], scan, [], restored)["regions"] == []


def test_partition_freezes_committed_regions_and_redivides_only_unowned_water():
    partition = kernel("partition", "partition_regions")
    scan = [[-1.0] * 40 for _ in range(40)]
    boats = fleet()
    initial = partition(boats, scan, [])
    dropped = [region for region in initial["regions"] if region["owner"] != boats[0]["id"]]
    result = partition(boats, scan, [], dropped)
    assert result["status"] == "succeeded", result
    kept = {region["owner"]: set(map(tuple, region["cells"])) for region in dropped}
    assert {region["owner"]: set(map(tuple, region["cells"])) for region in result["regions"] if region["owner"] in kept} == kept
    assert result["diagnostics"]["committed_regions"] == len(kept)
    assert result["diagnostics"]["local_repair"] is True
    assert result["diagnostics"]["repartitioned_regions"] == 1
    cells = [tuple(cell) for region in result["regions"] for cell in region["cells"]]
    assert len(cells) == len(set(cells)) == 1600


def test_partition_reports_information_statistics_and_priority_order():
    partition = kernel("partition", "partition_regions")
    scan = [[100.0] * 40 for _ in range(40)]
    for col in range(40):
        scan[col][col] = -1.0
    result = partition(fleet(2), scan, [])
    assert result["status"] == "succeeded"
    assert result["regions"] == sorted(result["regions"], key=lambda region: (-region["priority"], region["owner"]))
    assert sum(region["unseen_fraction"] * len(region["cells"]) for region in result["regions"]) == 40
    for region in result["regions"]:
        pending = sum(scan[cell[0]][cell[1]] < 0 for cell in region["cells"])
        assert len(region["scan_cells"]) == pending
        assert region["unseen_fraction"] == pytest.approx(pending/len(region["cells"]))
        assert region["overdue_cells"] == 0
        assert region["target_probability"] == 0.0
        assert region["search_cost_m"] == pytest.approx(region["workload"]*10000/560)
        assert region["mean_value"] == pytest.approx(region["workload"]/len(region["cells"]))
        assert region["max_value"] >= region["mean_value"]


def test_partition_evidence_field_balances_information_demand_not_area():
    partition = kernel("partition", "partition_regions")
    scan = [[-1.0] * 40 for _ in range(40)]
    evidence = [[0.0] * 40 for _ in range(40)]
    for col in range(8):
        for row in range(8):
            evidence[col][row] = 1.0
    plain = partition(fleet(2), scan, [])
    hot = partition(fleet(2), scan, [], None, target_evidence=evidence)
    assert plain["status"] == hot["status"] == "succeeded"
    assert sum(region["target_probability"] for region in hot["regions"]) == 64
    assert max(region["max_value"] for region in hot["regions"]) > 1.0

    def corner(result):
        return next(region for region in result["regions"] if (0, 0) in {tuple(cell) for cell in region["cells"]})
    assert len(corner(hot)["cells"]) < len(corner(plain)["cells"])
    assert corner(hot)["workload"] > len(corner(hot)["cells"])
    with pytest.raises(ValueError):
        partition(fleet(2), scan, [], None, target_evidence=[[0.0] * 39 for _ in range(40)])


def test_partition_obstacle_cells_and_disconnected_failure():
    partition = kernel("partition", "partition_regions")
    scan = [[-1.0] * 40 for _ in range(40)]
    obstacle = {"x": 2000, "y": 2000, "radius": 180}
    result = partition(fleet(), scan, [obstacle])
    assert result["status"] == "succeeded", result
    assert all(connected(r["cells"]) for r in result["regions"])
    for region in result["regions"]:
        for col, row in region["cells"]:
            nearest_x = max(col * 100, min(2000, (col+1) * 100))
            nearest_y = max(3900-row*100, min(2000, 4000-row*100))
            assert math.hypot(nearest_x-2000, nearest_y-2000) > 180
    wall = [{"x": 2000, "y": y, "radius": 160} for y in range(0, 4001, 200)]
    failed = partition(fleet(1), scan, wall)
    assert failed["status"] == "infeasible"
    assert not failed["regions"]


def test_region_search_is_closed_safe_and_curvature_limited():
    boat = fleet(1)[0]
    region = {"id": "test", "owner": boat["id"], "cells": [[x, y] for x in range(10) for y in range(20)], "bbox_m": [0, 2000, 1000, 4000]}
    result = kernel("coverage", "plan_region_search")(boat, region, [])
    assert result["status"] == "succeeded", result
    points = result["routes"][boat["id"]]
    assert points[0] == pytest.approx(boat["pose"])
    cycle_start = result["cycle_start_indices"][boat["id"]]
    assert points[-1] == pytest.approx(points[cycle_start])
    assert path_safe(points, [], [0, 0, 4000, 4000])
    for left, right in zip(points, points[1:]):
        distance = math.dist(left[:2], right[:2])
        assert distance <= 5.001
        assert abs(math.remainder(right[2]-left[2], 2*math.pi)) <= distance/60*1.001+1e-8


def test_tracking_team_has_moving_slots_transit_and_geometry():
    boats = fleet()
    contact = {"x": 2000, "y": 1800, "vx": 0.5, "vy": 0.1, "uncertainty_m": 40}
    result = kernel("tracking", "tracking_plan")(boats, contact, [])
    assert result["status"] == "succeeded", result
    assert 2 <= len(result["members"]) <= 3
    assert set(result["routes"]) == set(result["members"]) == set(result["slots"])
    assert result["diagnostics"]["geometry_quality"] > 0.15
    for identifier in result["members"]:
        boat = next(b for b in boats if b["id"] == identifier)
        points, slot = result["routes"][identifier], result["slots"][identifier]
        assert points[0] == pytest.approx(boat["pose"])
        assert points[-1] == pytest.approx(slot["pose"])
        assert path_safe(points, [], [0, 0, 4000, 4000])
        predicted = [contact["x"]+contact["vx"]*slot["eta_s"], contact["y"]+contact["vy"]*slot["eta_s"]]
        assert math.dist(slot["pose"][:2], predicted) < 350


def test_tracking_filters_energy_capability_and_preserves_input():
    boats = fleet()
    boats[0]["remaining_range_m"] = 1
    boats[1]["capabilities"] = ["active"]
    before = copy.deepcopy(boats)
    contact = {"x": 2000, "y": 1800, "vx": 0, "vy": 0, "uncertainty_m": 30}
    plan = kernel("tracking", "tracking_plan")
    result = plan(boats, contact, [])
    assert result["status"] == "succeeded"
    assert not {"uuv-0", "uuv-1"}.intersection(result["members"])
    assert boats == before
    assert plan(boats[:2], contact, [])["status"] == "infeasible"
    assert len(plan(boats[2:5], contact, [])["members"]) == 3


def test_local_partition_rechecks_energy_and_search_capability():
    partition = kernel("partition", "partition_regions")
    boats, scan = fleet(), [[-1.0] * 40 for _ in range(40)]
    initial = partition(boats, scan, [])
    boats[0]["remaining_range_m"] = 1
    assert partition(boats, scan, [], initial)["status"] == "infeasible"
    boats[0]["remaining_range_m"] = 50000
    boats[0]["capabilities"] = ["passive"]
    assert partition(boats, scan, [], initial)["status"] == "infeasible"


def test_region_search_l_shape_and_obstacle_detour():
    boat = fleet(1)[0]
    cells = [[col, row] for col in range(20) for row in range(20, 40) if col < 5 or row > 34]
    region = {"id": "elbow", "owner": boat["id"], "cells": cells}
    obstacles = [{"x": 1000, "y": 1500, "radius": 200}]
    plan = kernel("coverage", "plan_region_search")(boat, region, obstacles)
    assert plan["status"] == "succeeded", plan
    assert path_safe(plan["routes"][boat["id"]], obstacles, [0, 0, 4000, 4000])


def test_tracking_reports_energy_horizon_not_success_guarantee():
    result = kernel("tracking", "tracking_plan")(fleet(2), {"x": 1500, "y": 1500, "vx": 0, "vy": 0}, [])
    assert result["status"] == "succeeded"
    assert result["diagnostics"]["energy_horizon_s"] == 600
    assert result["diagnostics"]["arrival_is_tracking_success"] is False


def test_partition_workload_balanced_when_half_map_scanned():
    scan = [[500.0 if col < 20 else -1.0 for _ in range(40)] for col in range(40)]
    result = kernel("partition", "partition_regions")(fleet(), scan, [])
    assert result["status"] == "succeeded"
    workloads = [r["workload"] for r in result["regions"]]
    assert max(workloads)/min(workloads) < 1.6


@pytest.mark.parametrize("count", [2, 3])
def test_cooperative_control_sustains_bearing_geometry(count):
    control = kernel("tracking", "tracking_control")
    offsets = [0, math.pi/2] if count == 2 else [i*2*math.pi/3 for i in range(3)]
    poses = [[1500+220*math.cos(angle+0.1*i), 1500+220*math.sin(angle+0.1*i), angle+math.pi/2] for i, angle in enumerate(offsets)]
    minimum_quality = 1.0
    for tick in range(5000):
        estimate = {"x": 1500+0.35*tick*0.2, "y": 1500+0.1*tick*0.2, "vx": 0.35, "vy": 0.1}
        controls = [control(pose, estimate, slot=i, team=poses) for i, pose in enumerate(poses)]
        assert all(abs(curvature) <= 1/60 for curvature in controls)
        poses = [integrate(pose, 4, curvature, 0.2) for pose, curvature in zip(poses, controls)]
        if tick > 1000:
            bearings = [math.atan2(p[1]-estimate["y"], p[0]-estimate["x"]) for p in poses]
            quality = 1-math.hypot(sum(math.cos(2*a) for a in bearings), sum(math.sin(2*a) for a in bearings))/count
            minimum_quality = min(minimum_quality, quality)
            assert all(120 < math.dist(p[:2], [estimate["x"], estimate["y"]]) < 350 for p in poses)
    assert minimum_quality > 0.3


def test_boundary_entry_has_interior_closed_patrol():
    boat = {"id": "replacement", "pose": [40, 400, 0], "remaining_range_m": 18000}
    region = {"owner": boat["id"], "cells": [[x, y] for x in range(10) for y in range(20, 40)]}
    plan = kernel("coverage", "plan_region_search")(boat, region, [])
    assert plan["status"] == "succeeded", plan
    points = plan["routes"][boat["id"]]
    index = plan["cycle_start_indices"][boat["id"]]
    assert index > 0
    assert points[0] == boat["pose"]
    assert points[-1] == pytest.approx(points[index])
    assert min(points[index][0], points[index][1], 4000-points[index][0], 4000-points[index][1]) >= 140
    assert path_safe(points, [], [0, 0, 4000, 4000])


def test_local_energy_failure_falls_back_to_feasible_global_matching():
    boats = [{"id": "a", "pose": [3000, 2000, 0], "remaining_range_m": 6000},
             {"id": "b", "pose": [1000, 2000, 0], "remaining_range_m": 6000}]
    previous = [{"id": "old-a", "owner": "a", "cells": [[x, y] for x in range(20) for y in range(40)]},
                {"id": "old-b", "owner": "b", "cells": [[x, y] for x in range(20, 40) for y in range(40)]}]
    result = kernel("partition", "partition_regions")(boats, [[0.0]*40 for _ in range(40)], [], previous)
    assert result["status"] == "succeeded", result
    assert result["diagnostics"]["local_repair"] is False
    assert all(connected(region["cells"]) for region in result["regions"])


def test_cooperative_controller_captures_fast_target_from_outside_sensor_band():
    control = kernel("tracking", "tracking_control")
    poses = [[2486, 1702, -0.022], [2634, 1802, -0.019]]
    initial_estimate = {"x": 2961, "y": 1922, "vx": 2.11, "vy": 1.35}
    assert control(poses[0], initial_estimate, slot=0, team=poses) > 0.008
    for tick in range(600):
        estimate = {"x": 2961+2.11*tick*0.2, "y": 1922+1.35*tick*0.2, "vx": 2.11, "vy": 1.35}
        controls = [control(pose, estimate, slot=i, team=poses) for i, pose in enumerate(poses)]
        poses = [integrate(pose, 4, curve, 0.2) for pose, curve in zip(poses, controls)]
    assert all(math.dist(pose[:2], [estimate["x"], estimate["y"]]) < 430 for pose in poses)


def test_energy_bounded_patrol_discloses_deferred_work():
    boat = {"id": "a", "pose": [500, 500, 0], "remaining_range_m": 6000}
    region = {"owner": "a", "cells": [[x, y] for x in range(20) for y in range(40)]}
    result = kernel("coverage", "plan_region_search")(boat, region, [])
    assert result["status"] == "succeeded", result
    assert result["diagnostics"]["partial_patrol"] is True
    assert result["diagnostics"]["deferred_waypoints"] > 0
    assert result["diagnostics"]["coverage_guarantee"] is False
    assert result["requires_energy_rotation"] is True
    assert result["length_m"]+1100 <= boat["remaining_range_m"]


def test_partition_scan_cells_prioritize_unscanned_ledger():
    scan = [[10.0]*40 for _ in range(40)]
    scan[5][5] = -1.0
    result = kernel("partition", "partition_regions")(fleet(1), scan, [])
    assert result["status"] == "succeeded"
    assert result["regions"][0]["scan_cells"] == [[5, 5]]
    assert len(result["regions"][0]["cells"]) == 1600


def test_single_pending_cell_still_has_nonzero_forward_patrol():
    boat = {"id": "a", "pose": [500, 500, 0], "remaining_range_m": 18000}
    region = {"owner": "a", "cells": [[x, y] for x in range(20) for y in range(40)], "scan_cells": [[5, 5]]}
    result = kernel("coverage", "plan_region_search")(boat, region, [])
    assert result["status"] == "succeeded"
    points = result["routes"]["a"][result["cycle_start_indices"]["a"]:]
    assert sum(math.dist(a[:2], b[:2]) for a, b in zip(points, points[1:])) > 370


def test_boundary_pending_cell_patrol_keeps_controller_turn_clearance():
    boat = {"id": "a", "pose": [500, 500, 0], "remaining_range_m": 18000}
    region = {"owner": "a", "cells": [[5, 0]], "scan_cells": [[5, 0]]}
    result = kernel("coverage", "plan_region_search")(boat, region, [])
    assert result["status"] == "succeeded"
    patrol = result["routes"]["a"][result["cycle_start_indices"]["a"]:]
    assert path_safe(patrol, [], [0, 0, 4000, 4000], margin=60)


def test_disconnected_pending_cells_retain_executable_sweep():
    boat = {"id": "a", "pose": [400, 400, 0], "remaining_range_m": 50000}
    region = {"owner": "a", "cells": [[x, y] for x in range(40) for y in range(40)], "scan_cells": [[0, 0], [39, 39]]}
    result = kernel("coverage", "plan_region_search")(boat, region, [])
    assert result["status"] == "succeeded", result
    points = result["routes"]["a"]
    for x, y in ((50, 3950), (3950, 50)):
        assert min(math.dist(point[:2], [x, y]) for point in points) < 350


def test_partition_partial_energy_horizon_requires_explicit_policy_flag():
    boats = [{"id": "a", "pose": [1000, 2000, 0], "remaining_range_m": 3500},
             {"id": "b", "pose": [3000, 2000, 0], "remaining_range_m": 3500}]
    partition = kernel("partition", "partition_regions")
    scan = [[-1.0]*40 for _ in range(40)]
    assert partition(boats, scan, [])["status"] == "infeasible"
    for boat in boats:
        boat["allow_partial_patrol"] = True
    result = partition(boats, scan, [])
    assert result["status"] == "succeeded", result
    assert result["diagnostics"]["requires_energy_rotation"] is True
    assert sum(region["unscanned_cells"] for region in result["regions"]) == 1600
    assert sum(region["workload"] for region in result["regions"]) == 1600


def test_low_energy_patrol_enters_nearby_lane_middle_not_distant_endpoint():
    boat = {"id": "a", "pose": [2250, 1000, -math.pi/2], "remaining_range_m": 2400}
    region = {"owner": "a", "cells": [[x, y] for x in range(20, 30) for y in range(20, 40)]}
    result = kernel("coverage", "plan_region_search")(boat, region, [])
    assert result["status"] == "succeeded", result
    points = result["routes"]["a"]
    index = result["cycle_start_indices"]["a"]
    assert sum(math.dist(a[:2], b[:2]) for a, b in zip(points[:index+1], points[1:index+1])) < 100
    assert result["length_m"]+1600 <= 2400


def test_authorized_partial_owner_can_enter_near_region_edge():
    boat = {"id": "a", "pose": [350, 2000, 0], "remaining_range_m": 1900,
            "allow_partial_patrol": True}
    scan = [[-1.0]*40 for _ in range(40)]
    result = kernel("partition", "partition_regions")([boat], scan, [])
    assert result["status"] == "succeeded", result
    assert result["regions"][0]["unscanned_cells"] == 1600
    assert result["diagnostics"]["requires_energy_rotation"] is True


def test_authorized_local_revisit_defers_unreachable_scan_cells():
    boat = {"id": "a", "pose": [350, 2000, 0], "remaining_range_m": 2500,
            "allow_partial_patrol": True}
    region = {"owner": "a", "cells": [[x, y] for x in range(40) for y in range(40)],
              "scan_cells": [[39, 0]]}
    before = copy.deepcopy(region)
    result = kernel("coverage", "plan_region_search")(boat, region, [])
    assert result["status"] == "succeeded", result
    assert result["diagnostics"]["partial_patrol"] is True
    assert result["diagnostics"]["deferred_scan_cells"] == 1
    assert result["diagnostics"]["coverage_guarantee"] is False
    assert result["requires_replan_after_cycle"] is True
    assert region == before
    assert result["length_m"]+950 <= 2500
    assert path_safe(result["routes"]["a"], [], [0, 0, 4000, 4000])
    assert kernel("coverage", "plan_region_search")({**boat, "allow_partial_patrol": False}, region, [])["status"] == "infeasible"


def test_tracking_transit_respects_propagated_contact_envelope():
    boats = [{"id": "UUV-1", "pose": [1750, 1252.0524960987893, math.pi/2], "remaining_range_m": 15312},
             {"id": "UUV-3", "pose": [1750, 2952.0536588641326, math.pi/2], "remaining_range_m": 15312},
             {"id": "UUV-5", "pose": [2249.2405509177297, 1865.9736763590683, -1.5350041259034704], "remaining_range_m": 15312}]
    contact = {"x": 2348.8669729664703, "y": 1543.3295800352407,
               "vx": -.16938400043032098, "vy": 1.742494019116391, "uncertainty_m": 14.206740419235656,
               "covariance": [[47.04, 11.32, 8.66, 1.41], [11.32, 12.98, 1.34, 4.68],
                              [8.66, 1.34, 7.48, .997], [1.41, 4.68, .997, 4.46]]}
    result = kernel("tracking", "tracking_plan")(boats, contact, [{"x": 2500, "y": 2300, "radius": 140}])
    assert result["status"] == "succeeded", result
    assert len(result["members"]) == 3
    for points in result["routes"].values():
        elapsed = 0.0
        for left, right in zip(points, points[1:]):
            elapsed += math.dist(left[:2], right[:2])/4
            if elapsed >= 15:
                predicted = [contact["x"]+elapsed*contact["vx"], contact["y"]+elapsed*contact["vy"]]
                assert math.dist(right[:2], predicted) >= 100
