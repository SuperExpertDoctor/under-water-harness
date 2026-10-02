"""get_observations — paginated read of generated detection/bearing records."""

import copy

TOOL = {
    "name": "get_observations",
    "description": "Read already-generated active detections or passive "
                   "bearings. Paginate with cursor and limit; reading never "
                   "generates measurements. Passive records contain no "
                   "target position or range.",
    "snippet": "Read generated detection/bearing records",
    "guidelines": [
        "Paginate get_observations with cursor and limit; reading never "
        "generates new measurements.",
        "Passive bearing records carry no range or target position — infer "
        "position only from multi-boat geometry.",
    ],
    "parameters": {
        "type": "object",
        "properties": {
            "after_s": {"type": "number", "minimum": 0},
            "cursor": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "additionalProperties": False,
    },
    "mode": "lock",
}


def execute(runtime, data, worker=False):
    records = [o for o in runtime.observations
               if o["time_s"] > float(data.get("after_s", -1))
               and o.get("sequence", 0) > int(data.get("cursor", -1))]
    records = records[:min(100, max(1, int(data.get("limit", 50))))]
    return {"observations": copy.deepcopy(records),
            "cursor": records[-1]["sequence"] if records else runtime.observation_cursor}
