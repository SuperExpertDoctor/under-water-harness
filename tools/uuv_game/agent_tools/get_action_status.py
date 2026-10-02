"""get_action_status — query a candidate, plan, run or job by action_id."""

import copy

from ..runtime import MissionError

TOOL = {
    "name": "get_action_status",
    "description": "Query candidate, plan or run by action_id. Accepted is "
                   "not completed; pending approval means humans must "
                   "decide.",
    "snippet": "Query the status of an action, plan or run",
    "guidelines": [
        "accepted is not completed; check get_action_status before "
        "declaring a plan or run finished.",
    ],
    "parameters": {
        "type": "object",
        "properties": {"action_id": {"type": "string", "minLength": 1}},
        "required": ["action_id"],
        "additionalProperties": False,
    },
    "mode": "lock",
    "execution_mode": "sequential",
    "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
}


def execute(runtime, data, worker=False):
    action_id = data.get("action_id")
    result = (runtime.plans.get(action_id) or runtime.results.get(action_id)
              or runtime.receipts.get(action_id)
              or runtime.store.get_plan(action_id))
    result = result or next((j for j in runtime.agent_jobs
                             if j["run_id"] == action_id), None)
    if not result:
        raise MissionError("action_not_found", 404)
    return copy.deepcopy(runtime.summary(result))
