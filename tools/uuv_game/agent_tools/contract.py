"""Agent tool contract — the backend half of pi's ToolDefinition.

Each ``*.py`` file in ``tools/uuv_game/agent_tools/`` whose name does not
start with ``_`` defines one mission tool: a ``TOOL`` dict plus an
``execute`` function. Plugin files may additionally declare ``TOOLS``
(see ``plugins/contract.py``); those specs merge into the same catalog
and dispatch path, which is how a registered plugin becomes callable by
the PI agent without touching this package.

One tool spec mirrors pi's ToolDefinition four-piece split:

    model needs to know   -> description, snippet (-> promptSnippet),
                             guidelines (-> promptGuidelines)
    what it may pass      -> parameters (JSON-schema dict)
    sampling constraint   -> constrained_sampling
    does the work         -> execute(rt, data, worker) plus ``mode``

TOOL keys:

    name                  tool name the model calls. Plugin-declared tools
                          must be named ``"<plugin_id>__<suffix>"`` (plugin
                          id with '-' -> '_') so they can never shadow a
                          builtin.
    label                 UI label (defaults to name)
    description           model-facing description
    snippet               one-line Available-tools entry (required)
    guidelines            non-empty list of usage-rule strings (required)
    parameters            JSON-schema object dict for the arguments
    constrained_sampling  {"type": "json_schema", "strict": "prefer"|"require"}
    execution_mode        must be declared explicitly: "parallel" or
                          "sequential". Anything running under the runtime
                          lock — mode="lock", and every plugin-declared
                          tool — shares mission state and must declare
                          "sequential".
    mode                  "lock" (execute under runtime.lock) or
                          "calculate" (execute off the lock in a thread;
                          used by the planners). Plugin tools always run
                          under "lock".
    calls_model           optional bool; when True the result must carry a
                          "usage" key (token accounting) or the call fails
                          with tool_usage_missing.
    errors                optional list of documented failure codes.

    def execute(runtime, data, worker=False) -> dict:
        ``data`` is the raw tool-call params (validate it yourself).
        ``worker`` is True for calls arriving via /internal/tools/*.
        Raise MissionError(code, status) for domain errors — raising is
        the only way a tool reports failure; returning normally always
        means success.

Registration review (agent_tools/review.py) runs at package load for
builtins and inside plugins.install_plugin for TOOLS; a spec that fails
never reaches the catalog. Checks:

    1. Failure expression — with required params declared, execute(rt, {})
       must raise; a normal return is flagged as silent acceptance.
    2. Parallelism — execution_mode must be explicit; lock-mode tools may
       not declare "parallel" (shared mission state is queued, not raced).
    3. Queueing — mode must be "lock"/"calculate"; lock-mode execution
       serializes under runtime.lock in api.invoke.
    4. Accounting — calls_model results must include "usage" (enforced at
       resolve() time by an execute wrapper).
    Schema — description/snippet/guidelines non-empty; parameters
       type=object with required ⊆ properties; constrained_sampling is
       json_schema strict prefer|require.
"""

__all__ = []
