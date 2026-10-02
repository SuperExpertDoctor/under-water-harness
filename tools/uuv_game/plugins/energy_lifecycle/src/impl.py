"""Implementation of the energy-lifecycle plugin — internal; the
public interface is re-exported by the package __init__.
"""

from ....capabilities.lifecycle import prepare_exits



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
