"""plan_path — oriented Dubins-compatible path for one member."""

from ._schema import SNAPSHOT, algorithm, obj

TOOL = {
    "name": "plan_path",
    "description": "Calculate oriented Dubins-compatible path for ONE "
                   "member. goal=[x_m,y_m,heading_rad]. Geometry only; use "
                   "plan_search for executable looping missions.",
    "snippet": "Compute an oriented path for one member",
    "guidelines": [
        "plan_path is geometry-only for exactly one member; use "
        "plan_search for executable looping missions.",
    ],
    "parameters": obj({**SNAPSHOT,
                       "algorithm_id": algorithm("dubins_hybrid"),
                       "members": {"type": "array",
                                   "items": {"type": "string", "minLength": 1},
                                   "minItems": 1, "maxItems": 1},
                       "goal": {"type": "array",
                                "items": {"type": "number"},
                                "minItems": 3, "maxItems": 3,
                                "description": "[x_m,y_m,heading_rad]."}},
                      required=("members", "goal")),
    "mode": "calculate",
}


def execute(runtime, data, worker=False):
    return runtime.calculate("plan_path", data)
