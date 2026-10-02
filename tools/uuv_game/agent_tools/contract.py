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
    snippet               optional one-line Available-tools entry
    guidelines            optional list of usage-rule strings
    parameters            JSON-schema object dict for the arguments
    constrained_sampling  e.g. {"type": "json_schema", "strict": "prefer"}
    execution_mode        "parallel" (default) or "sequential" — declare
                          sequential when the tool mutates shared state
    mode                  "lock" (execute under runtime.lock) or
                          "calculate" (execute off the lock in a thread;
                          used by the planners). Plugin tools always run
                          under "lock".

    def execute(runtime, data, worker=False) -> dict:
        ``data`` is the raw tool-call params (validate it yourself).
        ``worker`` is True for calls arriving via /internal/tools/*.
        Raise MissionError(code, status) for domain errors.
"""

__all__ = []
