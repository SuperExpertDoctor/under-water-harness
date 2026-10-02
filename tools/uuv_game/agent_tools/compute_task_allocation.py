"""compute_task_allocation — candidate member/task allocation."""

from ._schema import SNAPSHOT, algorithm, obj

TOOL = {
    "name": "compute_task_allocation",
    "description": "Calculate candidate search assignments or two/three-boat "
                   "tracking teams for contact_id, with feasibility, energy "
                   "and coverage cost. Does not execute or produce an "
                   "executable mission.",
    "snippet": "Compute a candidate member/task allocation",
    "guidelines": [
        "compute_task_allocation returns a candidate only; check "
        "feasibility, energy and coverage cost before planning.",
    ],
    "parameters": obj({**SNAPSHOT,
                       "algorithm_id": algorithm("slot_assignment"),
                       "contact_id": {"type": "string", "minLength": 1},
                       "tasks": {
                           "type": "array", "maxItems": 8,
                           "items": obj({
                               "id": {"type": "string", "minLength": 1},
                               "center": {"type": "array",
                                          "items": {"type": "number"},
                                          "minItems": 2, "maxItems": 2},
                               "size": {"type": "integer", "minimum": 1, "maximum": 3},
                               "priority": {"type": "number", "minimum": 1, "maximum": 10},
                           }, required=("id", "center", "size", "priority")),
                       }}),
    "mode": "calculate",
    "execution_mode": "parallel",
    "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
}


def execute(runtime, data, worker=False):
    return runtime.calculate("compute_task_allocation", data)
