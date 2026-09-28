"""Linear assignment to task slots, not a coupled formation optimizer."""

import math

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config import algorithm_settings
from .motion import finite, valid_pose

_ASSIGNMENT = algorithm_settings("assignment")

def allocate_tasks(uuvs: list[dict], tasks: list[dict]) -> dict:
    if not isinstance(uuvs, list) or not isinstance(tasks, list):
        raise ValueError("vehicles and tasks must be lists")
    if any(not isinstance(uuv, dict) or not {"id", "pose"} <= uuv.keys() for uuv in uuvs):
        raise ValueError("vehicles require id and pose")
    if any(not isinstance(task, dict) or not {"id", "center", "size", "priority"} <= task.keys() for task in tasks):
        raise ValueError("tasks require id, center, size and priority")
    if len(uuvs) > _ASSIGNMENT["maximum_uuvs"] or len(tasks) > _ASSIGNMENT["maximum_uuvs"]:
        raise ValueError("at most eight vehicles and eight tasks are supported")
    ids = [uuv["id"] for uuv in uuvs]
    task_ids = [task["id"] for task in tasks]
    if any(not isinstance(identifier, str) or not identifier for identifier in ids + task_ids):
        raise ValueError("vehicle and task identifiers must be nonempty strings")
    if len(set(ids)) != len(ids) or len(set(task_ids)) != len(task_ids):
        raise ValueError("vehicle and task identifiers must be unique")
    poses = [valid_pose(uuv["pose"]) for uuv in uuvs]
    slots = []
    for task in tasks:
        size = task["size"]
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= _ASSIGNMENT["maximum_team_size"]:
            raise ValueError("team size must be an integer from one to three")
        priority = finite(task["priority"], "priority")
        if not _ASSIGNMENT["priority_min"] <= priority <= _ASSIGNMENT["priority_max"]:
            raise ValueError("priority must be between one and ten")
        center = task["center"]
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            raise ValueError("task center must contain x, y")
        center = [finite(value, "center") for value in center]
        slots.extend((task["id"], center, priority) for _ in range(size))
    result = {"status": "infeasible", "teams": [], "algorithm": "scipy-linear-sum-assignment-slots-v1"}
    if len(slots) > len(uuvs):
        return result
    if not slots:
        result["status"] = "succeeded"
        return result
    costs = np.array([[math.dist(pose[:2], center) * priority for _, center, priority in slots] for pose in poses])
    rows, columns = linear_sum_assignment(costs)
    assignments = {task["id"]: [] for task in tasks}
    for row, column in zip(rows, columns):
        assignments[slots[column][0]].append(ids[row])
    result.update(status="succeeded", teams=[{"id": f"team-{task['id']}", "task_id": task["id"], "members": assignments[task["id"]]} for task in tasks])
    return result
