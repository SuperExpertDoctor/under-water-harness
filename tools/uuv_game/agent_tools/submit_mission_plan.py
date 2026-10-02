"""submit_mission_plan — the only execution entry (mutating; sequential)."""

from ..runtime import MissionError

TOOL = {
    "name": "submit_mission_plan",
    "description": "Submit result_id with current episode_id, unique "
                   "command_id and a concise public decision_reason "
                   "explaining why the UUVs are scheduled. Backend may "
                   "return pending_approval. Only execution entry. Never "
                   "retry with different IDs blindly.",
    "snippet": "Submit a validated plan for execution",
    "guidelines": [
        "submit_mission_plan is the only execution entry; retry with the "
        "same result_id and episode_id but a fresh command_id.",
        "pending_approval means a human must decide — do not resubmit "
        "while pending.",
    ],
    "parameters": {
        "type": "object",
        "properties": {
            "episode_id": {"type": "string", "minLength": 1},
            "result_id": {"type": "string", "minLength": 1},
            "command_id": {"type": "string", "minLength": 1},
            "decision_reason": {
                "type": "string", "minLength": 1, "maxLength": 500,
                "description": "One concise PUBLIC explanation of why this "
                               "plan and these UUVs are scheduled. Do not "
                               "include private reasoning or secrets."},
        },
        "required": ["episode_id", "result_id", "command_id", "decision_reason"],
        "additionalProperties": False,
    },
    "execution_mode": "sequential",
    "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
    "mode": "lock",
}


def execute(runtime, data, worker=False):
    if not data.get("command_id"):
        raise MissionError("command_id_required", 422)
    if worker and (not isinstance(data.get("decision_reason"), str)
                   or not 1 <= len(data["decision_reason"].strip()) <= 500):
        raise MissionError("decision_reason_required", 422)
    return runtime.submit(data.get("result_id"), data["command_id"],
                          data.get("episode_id"), data.get("decision_reason"))
