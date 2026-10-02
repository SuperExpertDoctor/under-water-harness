"""Builtin plugin: cooperative path planning (virtual-leader trajectories)."""

PLUGIN = {
    "id": "path-planning", "name": "协同路径规划", "layer": 2, "color": "#4338ca",
    "desc": "虚拟领航点轨迹：引导 UUV 到站位、责任区或出口",
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("path-planning", "coverage-search"),
        ("path-planning", "coop-tracking"),
        ("path-planning", "uuv-control"),
    ],
    "owns_stages": ["motion"],
}


def activity(runtime, ctx):
    for uid in ctx.L["transit_track"] + ctx.L["transit_other"] + ctx.L["exiting"]:
        ctx.hit(uid)
    count = len(ctx.L["transit_track"]) + len(ctx.L["transit_other"]) + len(ctx.L["exiting"])
    ctx.meta(f"{count} 艇在途" if count else None)


EDGE_SUBJECTS = {
    "path-planning>coop-tracking": lambda L: L["transit_track"],
    "path-planning>coverage-search": lambda L: L["transit_other"],
    "path-planning>uuv-control": lambda L: L["moving"],
}
