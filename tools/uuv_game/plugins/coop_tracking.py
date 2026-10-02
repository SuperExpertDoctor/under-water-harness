"""Builtin plugin: cooperative target tracking (passive-forward sonar team)."""

PLUGIN = {
    "id": "coop-tracking", "name": "协同目标跟踪", "layer": 3, "color": "#be123c",
    "desc": "多艇前视被动声纳在协同区内编队稳定跟踪",
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("coop-tracking", "reacquire"),
        ("coop-tracking", "uuv-control"),
    ],
    "owns_stages": ["track_leases", "handover_prep", "motion", "observations"],
}


def activity(runtime, ctx):
    for uid in ctx.L["tracking"]:
        ctx.hit(uid)
    tracked = ctx.L["tracked"]
    ctx.meta("/".join(c["contact_id"] for c in tracked) + f" {tracked[0]['state']}" if tracked else None)


EDGE_SUBJECTS = {
    "coop-tracking>reacquire": lambda L: [c["contact_id"] for c in L["lost"]],
    "coop-tracking>uuv-control": lambda L: L["moving"],
}
