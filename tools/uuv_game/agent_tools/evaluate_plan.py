"""evaluate_plan — validate a plan candidate."""

TOOL = {
    "name": "evaluate_plan",
    "description": "Validate candidate result_id against current geometry, "
                   "members and permissions. Does not authorize or execute.",
    "snippet": "Validate a plan candidate",
    "guidelines": [
        "Run evaluate_plan on the result_id immediately before every "
        "submit_mission_plan.",
    ],
    "parameters": {
        "type": "object",
        "properties": {"result_id": {"type": "string", "minLength": 1}},
        "required": ["result_id"],
        "additionalProperties": False,
    },
    "mode": "lock",
    "execution_mode": "sequential",
    "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
}


def execute(runtime, data, worker=False):
    return runtime.evaluate(data.get("result_id"))
