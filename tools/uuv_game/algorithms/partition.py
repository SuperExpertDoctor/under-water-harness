"""Deterministic connected grid partitions, with committed-region repair."""

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
_PRIORITY = _PARTITION["region_priority"]


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
    search_cost = sum(weights[c] for c in cells)*_CELL**2/_PARTITION["effective_swath_m"]
    energy_horizon = min(search_cost, _PARTITION["energy_horizon_m"]) if boat.get("allow_partial_patrol") is True else search_cost
    entries = sorted(cells, key=lambda c: (math.dist([cx, cy], [(c[0]+.5)*_CELL, _WORLD-(c[1]+.5)*_CELL]), c))[:_PARTITION["entry_candidates"]]
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


def partition_regions(uuvs, scan_times, obstacles, previous=None, *, now=None, window_s=None, target_evidence=None):
    """Allocate 40x40 UI cells; never mutate the global scan-time ledger.

    A cell is searchable only if its whole square clears every obstacle. Still
    feasible prior regions stay frozen; only unowned free water is redivided
    among vehicles currently lacking a region, so executing search boundaries
    do not churn between planning rounds. Per-cell search demand combines scan
    staleness with the target-evidence field, making bisection balance
    information load rather than plain area.
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
    if target_evidence is not None and (len(target_evidence) != _GRID
            or any(not isinstance(column, (list, tuple)) or len(column) != _GRID for column in target_evidence)):
        raise ValueError("target_evidence must be a 40 by 40 column-major field")
    free, weights, evidence = set(), {}, {}
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
            hint = target_evidence[col][row] if target_evidence is not None else 0.0
            hint = min(1.0, max(0.0, float(hint))) if isinstance(hint, (int, float)) and math.isfinite(hint) else 0.0
            evidence[cell] = hint
            weights[cell] += _PARTITION["target_evidence_weight"]*hint
    result = {"status": "infeasible", "regions": [], "diagnostics": {
        "free_cells": len(free), "masked_cells": _GRID**2-len(free), "unowned_cells": len(free),
        "method": "committed-incremental-demand-bisection", "coverage_guarantee": False}}
    if not uuvs:
        result["status"] = "succeeded"
        return result
    if len(free) < len(uuvs):
        result["diagnostics"]["reason"] = "free-water components cannot satisfy one connected region per vehicle"
        return result
    old_regions = previous.get("regions", []) if isinstance(previous, dict) else previous or []
    boats = {u["id"]: u for u in uuvs}
    committed = {}
    for region in old_regions:
        owner = region.get("owner")
        if owner not in boats or not isinstance(region.get("cells"), list):
            continue
        usable = {tuple(cell) for cell in region["cells"] if isinstance(cell, (list, tuple)) and len(cell) == 2} & free
        if not usable:
            continue
        piece = max(_components(usable), key=lambda cells: (len(cells), sorted(cells)))
        if math.isfinite(_assignment_cost(boats[owner], piece, weights, obstacles)):
            committed[owner] = piece
    groups = None
    reason = "bounded connected bisection exhausted"
    pieces, dirty = [], True
    budget = len(committed)+len(free)+len(uuvs)+2
    while budget > 0:
        budget -= 1
        if dirty:
            held = set().union(*committed.values()) if committed else set()
            pieces = _components(free-held)
            dirty = False
        slots = [u for u in uuvs if u["id"] not in committed]
        if len(pieces) > len(slots):
            options = [(sum(weights[c] for c in piece), sum(weights[c] for c in owned), min(piece), owner, index)
                       for index, piece in enumerate(pieces) for owner, owned in committed.items()
                       if any(neighbor in owned for cell in piece for neighbor in _neighbors(cell))]
            if options:
                _, _, _, owner, index = min(options)
                committed[owner] |= pieces.pop(index)
                if not math.isfinite(_assignment_cost(boats[owner], committed[owner], weights, obstacles)):
                    committed.pop(owner)
                    dirty = True
                continue
            if committed:
                committed.pop(min(committed, key=lambda owner: (sum(weights[c] for c in committed[owner]), owner)))
                dirty = True
                continue
            reason = "free-water components cannot satisfy one connected region per vehicle"
            break
        if len(pieces) < len(slots):
            index = max(range(len(pieces)), key=lambda i: (sum(weights[c] for c in pieces[i]), min(pieces[i]))) if pieces else -1
            split = _split(pieces[index], weights) if pieces else None
            if split:
                pieces[index:index+1] = split
                continue
            if committed:
                committed.pop(max(committed, key=lambda owner: (sum(weights[c] for c in committed[owner]), owner)))
                dirty = True
                continue
            break
        if not slots:
            groups = committed
            break
        # Forbidden pairings remain infinite, not merely expensive assignments.
        costs = np.full((len(slots), len(pieces)), math.inf)
        for i, boat in enumerate(slots):
            for j, cells in enumerate(pieces):
                costs[i, j] = _assignment_cost(boat, cells, weights, obstacles)
        try:
            rows, columns = linear_sum_assignment(costs)
            feasible = len(rows) == len(slots) and all(math.isfinite(costs[i, j]) for i, j in zip(rows, columns))
        except ValueError:
            feasible = False
        if feasible:
            groups = dict(committed)
            for i, j in zip(rows, columns):
                groups[slots[i]["id"]] = set(pieces[j])
            break
        if committed:
            committed.clear()
            dirty = True
            continue
        reason = "no energy-feasible Dubins entry matching within connector budget"
        break
    if groups is None:
        result["diagnostics"]["reason"] = reason
        return result
    for owner in sorted(groups):
        cells = groups[owner]
        old = next((r for r in old_regions if r["owner"] == owner), None)
        pending = sorted(c for c in cells if scan_times[c[0]][c[1]] < 0 or latest-scan_times[c[0]][c[1]] > window_s)
        scan_cells = pending or sorted(cells, key=lambda c: (scan_times[c[0]][c[1]], c))[:max(1, math.ceil(len(cells)*_PARTITION["fallback_revisit_fraction"]))]
        workload = sum(weights[c] for c in cells)
        unscanned = sum(scan_times[c[0]][c[1]] < 0 for c in cells)
        overdue = sum(0 <= scan_times[c[0]][c[1]] < latest-window_s for c in cells)
        target_probability = sum(evidence[c] for c in cells)
        search_cost = workload*_CELL**2/_PARTITION["effective_swath_m"]
        priority = (_PRIORITY["unseen_weight"]*unscanned + _PRIORITY["value_weight"]*workload
                    + _PRIORITY["target_weight"]*target_probability + _PRIORITY["overdue_weight"]*overdue
                    - _PRIORITY["cost_weight"]*search_cost/_PRIORITY["cost_norm_m"])/len(cells)
        result["regions"].append({"id": old["id"] if old else f"region-{owner}", "owner": owner,
            "cells": [list(c) for c in sorted(cells)],
            "scan_cells": [list(c) for c in scan_cells],
            "bbox_m": [min(c[0] for c in cells)*_CELL, (_GRID-1-max(c[1] for c in cells))*_CELL,
                       (max(c[0] for c in cells)+1)*_CELL, (_GRID-min(c[1] for c in cells))*_CELL],
            "workload": workload, "unscanned_cells": unscanned,
            "mean_value": workload/len(cells), "max_value": max(weights[c] for c in cells),
            "unseen_fraction": unscanned/len(cells), "overdue_cells": overdue,
            "target_probability": target_probability, "search_cost_m": search_cost, "priority": priority})
    result["regions"].sort(key=lambda region: (-region["priority"], region["owner"]))
    result["status"] = "succeeded"
    result["diagnostics"].update(unowned_cells=0, local_repair=bool(committed),
        committed_regions=len(committed), repartitioned_regions=len(groups)-len(committed))
    if any(boat.get("allow_partial_patrol") is True for boat in uuvs):
        result["diagnostics"].update(requires_energy_rotation=True, assignment_energy_horizon_m=_PARTITION["energy_horizon_m"])
    return result
