"""Builtin plugin: area coverage search (side-scan + forward active sonar)."""

PLUGIN = {
    "id": "coverage-search", "name": "区域覆盖搜索", "layer": 3, "color": "#15803d",
    "desc": "侧扫 + 前视主动声纳沿责任区扫线持续覆盖",
    "inputs": "one", "outputs": "many", "core": True,
    "edges": [
        ("coverage-search", "coop-tracking"),
        ("coverage-search", "uuv-control"),
    ],
    "owns_stages": ["motion", "coverage_review"],
}


def activity(runtime, ctx):
    for uid in ctx.L["searching"]:
        ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['searching'])} 艇 · {len(ctx.L['owners'])} 责任区" if ctx.L["searching"] else None)


EDGE_SUBJECTS = {
    "coverage-search>coop-tracking": lambda L: [c["contact_id"] for c in L["contacts"]],
    "coverage-search>uuv-control": lambda L: L["moving"],
}
