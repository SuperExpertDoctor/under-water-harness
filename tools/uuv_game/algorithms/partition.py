"""Deterministic connected grid partitions, with local ownership repair."""

import math

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config import algorithm_settings
from .motion import finite, valid_pose
from .planning import environment, plan_path

_PARTITION = algorithm_settings("partition")
_CELL = _PARTITION["cell_m"]
_GRID = _PARTITION["grid_cells"]
_WORLD = _CELL * _GRID


def _neighbors(cell):
    x, y = cell
    return ((x-1, y), (x+1, y), (x, y-1), (x, y+1))


def _components(cells):
    remaining = set(cells)
    components = []
    while remaining:
        seed = min(remaining)
        remaining.remove(seed)
        found, pending = {seed}, [seed]
        while pending:
            for neighbor in _neighbors(pending.pop()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    found.add(neighbor)
                    pending.append(neighbor)
        components.append(found)
    return components


def _split(cells, weights, fraction=_PARTITION["balanced_split_fraction"]):
    """Prefer straight balanced cuts, requiring both children remain connected."""
    total = sum(weights[c] for c in cells)
    candidates = []
    for axis in (0, 1):
        width = max(c[axis] for c in cells)-min(c[axis] for c in cells)+1
        for cut in range(min(c[axis] for c in cells)+1, max(c[axis] for c in cells)+1):
            left = {c for c in cells if c[axis] < cut}
            right = cells-left
            imbalance = abs(sum(weights[c] for c in left)/total-fraction)
            candidates.append((imbalance + _PARTITION["balance_cut_penalty"]/width, axis, cut, left, right))
    for _, _, _, left, right in sorted(candidates, key=lambda value: value[:3]):
        if len(_components(left)) == len(_components(right)) == 1:
            return left, right
    return None


def _assignment_cost(boat, cells, weights, obstacles):
    capabilities = boat.get("capabilities", ["active", "passive"])
    if not any(capability in capabilities for capability in ("active", "search", "scan")):
        return math.inf
    cx = sum((c[0]+.5)*_CELL for c in cells)/len(cells)
    cy = sum(_WORLD-(c[1]+.5)*_CELL for c in cells)/len(cells)
    entry = min(cells, key=lambda c: (math.dist([cx, cy], [(c[0]+.5)*_CELL, _WORLD-(c[1]+.5)*_CELL]), c))
    search_cost = sum(weights[c] for c in cells)*_CELL**2/_PARTITION["effective_swath_m"]
    energy_horizon = min(search_cost, _PARTITION["energy_horizon_m"]) if boat.get("allow_partial_patrol") is True else search_cost
    entries = [entry]
    if boat.get("allow_partial_patrol") is True:
        entries += sorted(cells, key=lambda c: (math.dist(boat["pose"][:2], [(c[0]+.5)*_CELL, _WORLD-(c[1]+.5)*_CELL]), c))[:_PARTITION["partial_entry_candidates"]]
    for col, row in entries:
        x, y = (col+.5)*_CELL, _WORLD-(row+.5)*_CELL
        goal = [x, y, math.atan2(y-boat["pose"][1], x-boat["pose"][0])]
        path = plan_path(boat["pose"], goal, obstacles=obstacles, budget=_PARTITION["assignment_entry_budget"])
        exit_cost = min(x, y, _WORLD-x, _WORLD-y)+_PARTITION["exit_reserve_m"]
        if path["status"] == "succeeded" and path["length_m"]+energy_horizon+exit_cost <= boat.get("remaining_range_m", math.inf):
            return path["length_m"]+search_cost
    return math.inf


def partition_regions(uuvs, scan_times, obstacles, previous=None, *, now=None, window_s=None):
    """Allocate 40x40 UI cells; never mutate the global scan-time ledger.

    A cell is searchable only if its whole square clears every obstacle. Work
    estimates retain a revisit cost for scanned cells, so corridors stay owned.
    """
    circles, _ = environment(obstacles, (0, 0, _WORLD, _WORLD))
    window_s = _PARTITION["revisit_window_s"] if window_s is None else finite(window_s, "revisit window")
    if window_s <= 0:
        raise ValueError("revisit window must be positive")
    if not isinstance(uuvs, list) or len(uuvs) > 8:
        raise ValueError("partition requires at most eight vehicles")
    identifiers = [u.get("id") for u in uuvs]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("vehicle identifiers must be unique nonempty strings")
    for boat in uuvs:
        valid_pose(boat["pose"])
    if len(scan_times) != _GRID or any(len(column) != _GRID for column in scan_times):
        raise ValueError("scan_times must be a 40 by 40 column-major ledger")
    free, weights = set(), {}
    latest = max(0.0, max(finite(t, "scan time") for col in scan_times for t in col)) if now is None else finite(now, "current time")
    for col in range(_GRID):
        for row in range(_GRID):
            if any(math.hypot(max(col*_CELL, min(x, (col+1)*_CELL))-x,
                              max(_WORLD-(row+1)*_CELL, min(y, _WORLD-row*_CELL))-y) <= radius+_PARTITION["obstacle_margin_m"]
                   for x, y, radius in circles):
                continue
            cell = (col, row)
            free.add(cell)
            timestamp = scan_times[col][row]
            weights[cell] = 1.0 if timestamp < 0 else _PARTITION["visited_workload_floor"]+_PARTITION["visited_workload_scale"]*min(1.0, max(0, latest-timestamp)/window_s)
    result = {"status": "infeasible", "regions": [], "diagnostics": {
        "free_cells": len(free), "masked_cells": _GRID**2-len(free), "unowned_cells": len(free),
        "method": "connected-workload-bisection", "coverage_guarantee": False}}
    if not uuvs:
        result["status"] = "succeeded"
        return result
    components = _components(free)
    if len(components) > len(uuvs) or len(free) < len(uuvs):
        result["diagnostics"]["reason"] = "free-water components cannot satisfy one connected region per vehicle"
        return result
    old_regions = previous.get("regions", []) if isinstance(previous, dict) else previous or []
    groups = {r["owner"]: set(map(tuple, r["cells"])) for r in old_regions}
    old_cells = [cell for group in groups.values() for cell in group]
    local = bool(groups) and set(old_cells) == free and len(old_cells) == len(free) and all(len(_components(group)) == 1 for group in groups.values())
    if local:
        for removed in sorted(set(groups)-set(identifiers)):
            orphan = groups[removed]
            adjacent = [owner for owner, cells in groups.items() if owner != removed and any(neighbor in cells for c in orphan for neighbor in _neighbors(c))]
            if not adjacent:
                local = False
                break
            recipient = min(adjacent, key=lambda owner: (owner not in identifiers, sum(weights[c] for c in groups[owner]), owner))
            groups[recipient].update(groups.pop(removed))
        if local:
            for added in sorted(set(identifiers)-set(groups)):
                choices = sorted(groups, key=lambda owner: (-sum(weights[c] for c in groups[owner]), owner))
                for owner in choices:
                    split = _split(groups[owner], weights)
                    if split:
                        boat = next(u for u in uuvs if u["id"] == added)
                        ordered = sorted(split, key=lambda cells: min(math.dist(boat["pose"][:2], [(c[0]+.5)*_CELL, _WORLD-(c[1]+.5)*_CELL]) for c in cells))
                        groups[added], groups[owner] = ordered
                        break
                else:
                    local = False
                    break
    if local and any(not math.isfinite(_assignment_cost(boat, groups[boat["id"]], weights, obstacles)) for boat in uuvs):
        local = False
        result["diagnostics"]["local_fallback_reason"] = "local ownership repair fails capability, entry or energy feasibility"
    if not local:
        pieces = components[:]
        while len(pieces) < len(uuvs):
            split = None
            for index in sorted(range(len(pieces)), key=lambda i: -sum(weights[c] for c in pieces[i])):
                split = _split(pieces[index], weights)
                if split:
                    pieces[index:index+1] = split
                    break
            if not split:
                result["diagnostics"]["reason"] = "bounded connected bisection exhausted"
                return result
        # Forbidden pairings remain infinite, not merely expensive assignments.
        costs = np.full((len(uuvs), len(pieces)), math.inf)
        for i, boat in enumerate(uuvs):
            for j, cells in enumerate(pieces):
                costs[i, j] = _assignment_cost(boat, cells, weights, obstacles)
        try:
            rows, columns = linear_sum_assignment(costs)
        except ValueError:
            result["diagnostics"]["reason"] = "no energy-feasible Dubins entry matching within connector budget"
            return result
        groups = {uuvs[i]["id"]: pieces[j] for i, j in zip(rows, columns)}
    for owner in sorted(groups):
        cells = groups[owner]
        old = next((r for r in old_regions if r["owner"] == owner), None)
        pending = sorted(c for c in cells if scan_times[c[0]][c[1]] < 0 or latest-scan_times[c[0]][c[1]] > window_s)
        scan_cells = pending or sorted(cells, key=lambda c: (scan_times[c[0]][c[1]], c))[:max(1, math.ceil(len(cells)*_PARTITION["fallback_revisit_fraction"]))]
        result["regions"].append({"id": old["id"] if old else f"region-{owner}", "owner": owner,
            "cells": [list(c) for c in sorted(cells)],
            "scan_cells": [list(c) for c in scan_cells],
            "bbox_m": [min(c[0] for c in cells)*_CELL, (_GRID-1-max(c[1] for c in cells))*_CELL,
                       (max(c[0] for c in cells)+1)*_CELL, (_GRID-min(c[1] for c in cells))*_CELL],
            "workload": sum(weights[c] for c in cells), "unscanned_cells": sum(scan_times[c[0]][c[1]] < 0 for c in cells)})
    result["status"] = "succeeded"
    result["diagnostics"].update(unowned_cells=0, local_repair=local)
    if any(boat.get("allow_partial_patrol") is True for boat in uuvs):
        result["diagnostics"].update(requires_energy_rotation=True, assignment_energy_horizon_m=_PARTITION["energy_horizon_m"])
    return result
