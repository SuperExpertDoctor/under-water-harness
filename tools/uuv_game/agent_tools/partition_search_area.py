"""partition_search_area — candidate connected-region split for search."""

from ._schema import FLEET, SNAPSHOT, algorithm, obj

TOOL = {
    "name": "partition_search_area",
    "description": "Calculate connected one-boat-per-region search "
                   "responsibilities from coverage staleness and target "
                   "evidence; executing regions keep their boundaries and "
                   "only unowned water is redivided. Omitted members uses "
                   "available search boats. Candidate only; no assignment "
                   "or movement.",
    "snippet": "Compute a candidate search-area partition",
    "guidelines": [
        "partition_search_area returns a candidate only — it never moves "
        "UUVs or assigns tasks.",
    ],
    "parameters": obj({**SNAPSHOT,
                       "algorithm_id": algorithm("connected_partition"),
                       "members": FLEET}),
    "mode": "calculate",
    "execution_mode": "parallel",
    "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
}


def execute(runtime, data, worker=False):
    return runtime.calculate("partition_search_area", data)
