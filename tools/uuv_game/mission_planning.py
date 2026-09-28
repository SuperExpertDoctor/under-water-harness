"""Compose pure per-boat algorithms into atomic mission candidates."""

from .algorithms.partition import partition_regions
from .algorithms.coverage import plan_region_search
from .algorithms.partition import _components
from .algorithms.planning import path_safe


def explicit_regions(boats, bbox, obstacles, previous):
    """Reserve nonoverlapping connected cells for an explicitly scoped request."""
    members = {boat["id"] for boat in boats}
    owned = {tuple(cell) for region in previous if region["owner"] not in members for cell in region["cells"]}
    x0, y0, x1, y1 = bbox
    width = (x1-x0)/len(boats)
    regions = []
    for index, boat in enumerate(boats):
        bounds = [x0+index*width, y0, x0+(index+1)*width, y1]
        cells = {(c, r) for c in range(40) for r in range(40) if (c, r) not in owned
                 and bounds[0] <= c*100+50 < bounds[2] and y0 <= 3950-r*100 < y1
                 and path_safe([[c*100+50, 3950-r*100, 0]], obstacles, (0, 0, 4000, 4000), margin=80)}
        if not cells or len(_components(cells)) != 1:
            return None
        regions.append({"id": f"region-{boat['id']}", "owner": boat["id"], "bbox_m": bounds, "cells": [list(cell) for cell in sorted(cells)]})
    return regions


def search_bundle(boats, scan_times, obstacles, previous=None, allow_partial=False, route_obstacles=None):
    boats = [{**boat, "allow_partial_patrol": True} for boat in boats] if allow_partial else boats
    partition = partition_regions(boats, scan_times, obstacles, previous)
    result = {**partition, "kind": "search", "algorithm": "connected-coverage-dubins-v2", "routes": {},
              "bbox": [0, 0, 4000, 4000], "fleet_plan": True, "cycle_start_indices": {}}
    if partition["status"] != "succeeded":
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
    return result
