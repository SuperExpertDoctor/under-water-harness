"""Builtin plugin: dynamic task-region partitioning."""

PLUGIN = {
    "id": "region-partition", "name": "动态任务区域划分", "layer": 1, "color": "#0369a1",
    "desc": "把搜索海域划为责任区，随能源与覆盖动态修补",
    "inputs": "one", "outputs": "many", "core": True,
    "edges": [
        ("region-partition", "task-allocation"),
    ],
    "owns_stages": ["contact_repairs", "coverage_review"],
}


def activity(runtime, ctx):
    for uid in ctx.L["owners"]:
        ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['owners'])} 责任区" if ctx.L["owners"] else None)


EDGE_SUBJECTS = {
    "region-partition>task-allocation": lambda L: L["owners"],
}
