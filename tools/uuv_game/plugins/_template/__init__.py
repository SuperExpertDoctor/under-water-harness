"""Plugin template — a plugin is a FOLDER, not a file.

Copy this directory to ``plugins/<my-plugin>/`` (underscore-free, kebab
or snake name), edit ``PLUGIN.id`` and you are registered at the next
reload. The public interface is exactly what this file exports:

    PLUGIN          spec dict (required) — see contract.py for every key
    activity        optional fn(runtime, ctx) -> per-frame real invocations
    EDGE_SUBJECTS   optional {"from>to": fn(L) -> [labels]} on active edges
    STAGES          optional {"slot": fn(rt) -> bool|None} (core plugins only)
    tick_stages     optional fn(runtime) -> [{"after": slot, "fn": fn}]
                    — custom plugins insert positioned stages this way
    TOOLS           optional {"<plugin_id>__<suffix>": spec} — agent tools,
                    reviewed at install/load (see agent_tools/review.py)

Everything else lives under ``src/`` — split implementation across as
many modules as needed; only this __init__ is the interface. Put the
usage guide in ``SKILL.md``.
"""

from .src import impl

PLUGIN = {
    # identity + card presentation (required)
    "id": "my-plugin", "name": "示例插件", "layer": 2, "color": "#0ea5e9",
    "desc": "一句话说明这个插件在算法中干什么",
    "snippet": "插件卡片上的短句（进入注册审查的提示词贡献）",
    "guidelines": ["插件卡片 tooltip 上的用法说明"],

    # ports (required): "none" | "one" | "many"
    "inputs": "one", "outputs": "many",

    # False for every custom plugin — core plugins refuse disable()
    "core": False,

    # topology edges: ("from","to") or {"from","to","always_active":True}
    "edges": [("my-plugin", "uuv-control")],

    # slots this plugin owns (STAGE_SLOTS names; core only for now)
    "owns_stages": [],
}

# The single exposed interface — re-export impl bodies as needed.
activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
tick_stages = impl.tick_stages

# Agent-callable tools: reviewed at install/load; a spec that fails
# never reaches the catalog. See agent_tools/review.py for the rules.
TOOLS = {
    "my_plugin__probe": {
        "description": "描述这个工具让模型知道什么时候调用它",
        "snippet": "一行摘要，进入系统提示词的 Available tools",
        "guidelines": ["给模型的使用规则"],
        "parameters": {
            "type": "object",
            "properties": {"uuv_id": {"type": "string", "minLength": 1}},
            "required": ["uuv_id"], "additionalProperties": False,
        },
        "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
        # plugin tools run under runtime.lock — shared state is queued,
        # so "parallel" is rejected by review
        "execution_mode": "sequential",
        # optional: True => result must contain "usage" (token accounting)
        "calls_model": False,
        "errors": ["uuv_id_required"],
        "execute": impl.probe,
    },
}
