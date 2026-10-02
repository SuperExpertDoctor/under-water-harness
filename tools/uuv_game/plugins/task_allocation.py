"""Builtin plugin: task allocation (approved plan members -> UUVs)."""

PLUGIN = {
    "id": "task-allocation", "name": "任务分配", "layer": 1, "color": "#1d4ed8",
    "desc": "把获批计划的成员 / 责任区指派到具体 UUV",
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("task-allocation", "coverage-search"),
        ("task-allocation", "path-planning"),
        ("task-allocation", "coop-tracking"),
        ("task-allocation", "reacquire"),
    ],
    "owns_stages": ["plan_lifecycle"],
}


def activity(runtime, ctx):
    if ctx.L["allocated"]:
        for uid in ctx.L["allocated"]:
            ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['plans'])} 个活跃计划" if ctx.L["plans"] else None)


EDGE_SUBJECTS = {
    "task-allocation>coverage-search": lambda L: L["searching"],
    "task-allocation>path-planning": lambda L: L["transit_track"] + L["transit_other"] + L["exiting"],
    "task-allocation>coop-tracking": lambda L: L["tracking"],
    "task-allocation>reacquire": lambda L: L["reacquire_members"],
}
