"""Builtin plugin: energy lifecycle (fuel watch, exits, replacements).

Owns the exit-preparation pipeline stage.
"""

from ..capabilities.lifecycle import prepare_exits

PLUGIN = {
    "id": "energy-lifecycle", "name": "能源轮换", "layer": 0, "color": "#0f766e",
    "desc": "油量监视、低油量返航、替补艇生成与跟踪交接",
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("energy-lifecycle", "task-allocation"),
        ("energy-lifecycle", "path-planning"),
        {"from": "energy-lifecycle", "to": "region-partition", "always_active": True},
    ],
    "owns_stages": ["exit_prep", "motion"],
}


def activity(runtime, ctx):
    for uid in ctx.L["exiting"] + ctx.L["relief"] + ctx.L["low_fuel"]:
        ctx.hit(uid)
    count = len(set(ctx.L["exiting"] + ctx.L["relief"] + ctx.L["low_fuel"]))
    ctx.meta(f"{count} 艇轮换中" if count else None)


EDGE_SUBJECTS = {
    "energy-lifecycle>path-planning": lambda L: L["exiting"],
    "energy-lifecycle>task-allocation": lambda L: L["relief"] + L["low_fuel"],
}


def stage_exit_prep(rt):
    return prepare_exits(rt)


STAGES = {"exit_prep": stage_exit_prep}
