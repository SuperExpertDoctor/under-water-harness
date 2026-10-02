"""Compose pure per-boat algorithms into atomic mission candidates."""

from .config import Config, algorithm_settings
from .algorithms.partition import partition_regions
from .algorithms.coverage import plan_region_search
from .algorithms.partition import _components
from .algorithms.planning import path_safe
from .algorithms.conflicts import resolve_conflicts

_MISSION = algorithm_settings("mission_planning")


def explicit_regions(boats, bbox, obstacles, previous):
    """Reserve nonoverlapping connected cells for an explicitly scoped request."""
    members = {boat["id"] for boat in boats}
    owned = {tuple(cell) for region in previous if region["owner"] not in members for cell in region["cells"]}
    x0, y0, x1, y1 = bbox
    width = (x1-x0)/len(boats)
    regions = []
    config = Config()
    columns, rows = int(config.width/config.cell), int(config.height/config.cell)
    for index, boat in enumerate(boats):
        bounds = [x0+index*width, y0, x0+(index+1)*width, y1]
        cells = {(c, r) for c in range(columns) for r in range(rows) if (c, r) not in owned
                 and bounds[0] <= (c+.5)*config.cell < bounds[2]
                 and y0 <= config.height-(r+.5)*config.cell < y1
                 and path_safe([[(c+.5)*config.cell, config.height-(r+.5)*config.cell, 0]], obstacles,
                               (0, 0, config.width, config.height), margin=_MISSION["explicit_region_obstacle_margin_m"])}
        if not cells or len(_components(cells)) != 1:
            return None
        regions.append({"id": f"region-{boat['id']}", "owner": boat["id"], "bbox_m": bounds, "cells": [list(cell) for cell in sorted(cells)]})
    return regions


def search_bundle(boats, scan_times, obstacles, previous=None, allow_partial=False, route_obstacles=None,
                  now=None, window_s=None, target_evidence=None):
    boats = [{**boat, "allow_partial_patrol": True} for boat in boats] if allow_partial else boats
    partition = partition_regions(boats, scan_times, obstacles, previous, now=now, window_s=window_s,
        target_evidence=target_evidence)
    result = {**partition, "kind": "search", "algorithm": "connected-coverage-dubins-v2", "routes": {},
              "bbox": [0, 0, Config().width, Config().height], "fleet_plan": True, "cycle_start_indices": {}}
    if partition["status"] != "succeeded":
        return result
    if not boats:
        return result
    if partition["diagnostics"].get("requires_energy_rotation"):
        result["requires_energy_rotation"] = True
    for region in partition["regions"]:
        boat = next(u for u in boats if u["id"] == region["owner"])
        # Temporary vehicle collision envelopes must not remove task-area ownership.
        route = plan_region_search(boat, region, obstacles if route_obstacles is None else route_obstacles)
        if route["status"] != "succeeded":
            result.update(status=route["status"], diagnostics={"reason": "region_route_failed", "owner": boat["id"], "details": route["diagnostics"]})
            return result
        result["routes"].update(route["routes"])
        result["cycle_start_indices"].update(route.get("cycle_start_indices", {}))
        if route.get("requires_energy_rotation"):
            result["requires_energy_rotation"] = True
            result["requires_replan_after_cycle"] = True
            result.setdefault("partial_patrols", {})[boat["id"]] = route["diagnostics"]
    starts = {boat["id"]: boat["pose"] for boat in boats}
    joint = resolve_conflicts(starts, {name: path[-1] for name, path in result["routes"].items()},
        result["routes"], obstacles if route_obstacles is None else route_obstacles,
        cycle_start_indices=result["cycle_start_indices"])
    result["diagnostics"]["conflict_solver"] = joint["diagnostics"]["algorithm"]
    if joint["status"] != "succeeded":
        result.update(status="infeasible", routes={}, diagnostics={**result["diagnostics"],
            "reason": "joint_route_conflict", "conflict": joint["diagnostics"]})
        return result
    result["routes"] = joint["routes"]
    result["cycle_start_indices"] = joint["cycle_start_indices"]
    return result
