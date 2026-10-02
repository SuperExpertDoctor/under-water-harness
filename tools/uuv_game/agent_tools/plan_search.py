"""plan_search — candidate coverage-search plan."""

from ._schema import BBOX, FLEET, SNAPSHOT, algorithm, obj

TOOL = {
    "name": "plan_search",
    "description": "For automatic whole-area coverage OMIT BOTH members and "
                   "bbox: plan_search({standing_policy:true}) when that "
                   "policy is requested. The backend selects available "
                   "search boats and partitions the entire searchable sea "
                   "into connected regions. Never add bbox=[0,0,4000,4000] "
                   "to this automatic call. Explicit members+bbox are only "
                   "for an explicitly requested scoped search, not a "
                   "fallback for failed global planning. Includes feasible "
                   "closed search routes. standing_policy adds bounded "
                   "energy rotation and local repair to approval. Returns "
                   "result_id; no execution.",
    "snippet": "Compute a candidate coverage-search plan",
    "guidelines": [
        "For automatic whole-area coverage omit both members and bbox; "
        "never pass bbox=[0,0,4000,4000] as a fallback.",
        "Every candidate must pass evaluate_plan before submit_mission_plan.",
    ],
    "parameters": obj({**SNAPSHOT,
                       "algorithm_id": algorithm("strip_coverage"),
                       "members": FLEET,
                       "bbox": BBOX,
                       "mode": {"type": "string", "enum": ["search", "reacquire"]},
                       "standing_policy": {
                           "type": "boolean",
                           "description": "Include bounded energy exit, "
                                          "boundary replacement and local "
                                          "coverage repair in this plan's "
                                          "approval. Not an approval "
                                          "itself."}}),
    "mode": "calculate",
}


def execute(runtime, data, worker=False):
    return runtime.calculate("plan_search", data)
