"""Builtin plugin: lost-contact reacquisition (re-search -> redetect -> track)."""

PLUGIN = {
    "id": "reacquire", "name": "失联再捕获", "layer": 3, "color": "#a16207",
    "desc": "目标丢失后重搜该区域、再发现并恢复跟踪",
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("reacquire", "coop-tracking"),
        ("reacquire", "uuv-control"),
    ],
    "owns_stages": ["contact_repairs", "motion"],
}


def activity(runtime, ctx):
    for uid, action in runtime.active.items():
        if action.get("kind") == "reacquire":
            ctx.hit(uid)
    for uid in ctx.L["reacquire_members"]:
        ctx.hit(uid)
    if ctx.L["lost"]:
        ctx.hit()
    lost = ctx.L["lost"]
    ctx.meta("/".join(c["contact_id"] for c in lost) + " 丢失重搜" if lost else None)


EDGE_SUBJECTS = {
    "reacquire>coop-tracking": lambda L: [c["contact_id"] for c in L["tracked"]],
    "reacquire>uuv-control": lambda L: L["moving"],
}
