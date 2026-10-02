"""get_mission_state — read the authoritative observed mission snapshot."""

TOOL = {
    "name": "get_mission_state",
    "description": "Read authoritative observed mission snapshot, current "
                   "episode, revision, UUV poses, contacts and permissions. "
                   "No target truth.",
    "snippet": "Read the observed mission snapshot",
    "guidelines": [
        "Call get_mission_state before planning or submitting; reuse its "
        "episode_id and mission_revision.",
    ],
    "parameters": {"type": "object", "properties": {},
                   "additionalProperties": False},
    "mode": "lock",
    "execution_mode": "sequential",
    "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
}


def execute(runtime, data, worker=False):
    return runtime.mission_state()
