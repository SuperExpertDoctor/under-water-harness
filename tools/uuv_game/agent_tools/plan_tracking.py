"""plan_tracking — candidate cooperative passive-bearing tracking plan."""

from ._schema import SNAPSHOT, algorithm, obj

TOOL = {
    "name": "plan_tracking",
    "description": "Calculate cooperative passive-bearing tracking for "
                   "confirmed contact_id. Omit members to select a feasible "
                   "two/three-boat team automatically. Includes observation "
                   "geometry, member-specific transit, acquisition "
                   "conditions, energy and remaining search coverage "
                   "repair. Accepted/transit is not effective tracking. "
                   "Returns result_id; no execution.",
    "snippet": "Compute a candidate cooperative-tracking plan",
    "guidelines": [
        "Omit members in plan_tracking to auto-select a feasible team; "
        "accepted or transit is not effective tracking.",
    ],
    "parameters": obj({**SNAPSHOT,
                       "algorithm_id": algorithm("distance_band"),
                       "contact_id": {"type": "string", "minLength": 1},
                       "members": {"type": "array",
                                   "items": {"type": "string", "minLength": 1},
                                   "minItems": 2, "maxItems": 3,
                                   "uniqueItems": True}},
                      required=("contact_id",)),
    "mode": "calculate",
}


def execute(runtime, data, worker=False):
    return runtime.calculate("plan_tracking", data)
