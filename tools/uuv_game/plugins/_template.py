"""Plugin template — copy this file to a new name in this directory and edit.

The loader imports every *.py here whose name does not start with '_' and
registers it if it defines PLUGIN. See contract.py for the full contract.

Example: cp _template.py my_plugin.py  →  edit PLUGIN + hooks  →  restart.
"""

PLUGIN = {
    # ---- required ----
    "id": "my-plugin",            # unique kebab-case id
    "name": "我的插件",            # display name in the library + graph
    "layer": 3,                   # graph column depth (roots = 0)
    "color": "#7c3aed",           # node accent color
    "desc": "一句话描述这个控制算法做什么",
    "inputs": "one",              # port cardinality: none | one | many
    "outputs": "many",
    "core": False,                # core plugins cannot be disabled

    # ---- optional ----
    # one-line card summary (falls back to desc); the promptSnippet analog
    "snippet": "短句说明，显示在插件卡片上",
    # usage notes shown on the card tooltip; the promptGuidelines analog
    "guidelines": ["什么时候依赖这个插件", "使用时需要注意的约束"],
    # outgoing graph edges; "always_active" keeps an edge lit without subjects
    "edges": [("task-allocation", "my-plugin"), ("my-plugin", "uuv-control")],
    # pipeline slots this plugin owns (all owners disabled => slot skipped)
    # slot names: track_leases contact_repairs handover_prep exit_prep motion
    #             observations scene plan_lifecycle coverage_review
    "owns_stages": [],
}


def activity(runtime, ctx):
    """Called every frame. Mark this plugin's real invocations on the graph.

    ctx.hit(subject)  -> light the node, record a subject (UUV-N / CONTACT-N)
    ctx.meta(text)    -> status line inside the node box
    ctx.L             -> shared per-frame lists (see contract.py)
    """
    for uid in ctx.L["moving"]:
        if uid.startswith("UUV-99"):
            ctx.hit(uid)
    ctx.meta(None)


# Edge subject resolvers: what flows across each live edge this frame.
EDGE_SUBJECTS = {
    "my-plugin>uuv-control": lambda L: L["moving"],
}


def tick_stages(runtime):
    """Optional: insert custom work into the tick pipeline (non-core only).

    Return entries {"after": <slot name>, "fn": fn(runtime) -> bool|None};
    fn returning False aborts the tick (pause semantics). Unknown slot names
    append at the end of the pipeline.
    """
    return []


def _probe(runtime, params, worker=False):
    """Tool body: runs under runtime.lock. Params arrive as a plain dict —
    validate defensively. Raise MissionError(code, status) for domain
    errors; anything else becomes a generic tool failure. Registration
    review requires execute() to raise on missing required params —
    returning normally is treated as silently accepting invalid input."""
    if not params.get("uuv_id"):
        raise ValueError("uuv_id_required")
    return {"uuv_id": params.get("uuv_id"), "status": "ok"}


# Optional: agent-callable tools exposed by this plugin — the plugin-side
# half of pi's ToolDefinition. Names must start with "<plugin-id>__" so a
# plugin can never shadow a builtin tool. Each entry is registered in the
# shared catalog; the PI worker adapts it into registerTool() for you.
TOOLS = {
    "my_plugin__probe": {
        "description": "描述这个工具让模型知道什么时候调用它",
        "snippet": "一行摘要，进入系统提示词的 Available tools",
        "guidelines": ["给模型的使用规则"],
        "parameters": {
            "type": "object",
            "properties": {
                "uuv_id": {"type": "string", "minLength": 1},
            },
            "required": ["uuv_id"],
            "additionalProperties": False,
        },
        # required: {"type": "json_schema", "strict": "prefer"|"require"}
        "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
        # required and must be "sequential": plugin tools run under the
        # runtime lock (shared mission state), so parallel declaration is
        # rejected by review
        "execution_mode": "sequential",
        # optional: True when execute internally calls a model — the result
        # must then include a "usage" key (token accounting) or the call
        # fails with tool_usage_missing
        "calls_model": False,
        # optional: documented failure codes surfaced to operators
        "errors": ["uuv_id_required"],
        "execute": _probe,
    },
}
