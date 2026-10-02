"""Builtin plugin: multi-UUV bearing fusion (EKF contact estimation)."""

PLUGIN = {
    "id": "sensor-fusion", "name": "多艇方位融合", "layer": 2, "color": "#0e7490",
    "desc": "≥2 艇前视被动方位观测经 EKF 融合为目标估计",
    "inputs": "many", "outputs": "one", "core": True,
    "edges": [
        ("sensor-fusion", "coop-tracking"),
    ],
    "owns_stages": ["observations"],
}


def activity(runtime, ctx):
    if len(ctx.L["observers"]) >= 2:
        for uid in ctx.L["observers"]:
            ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['observers'])} 源方位融合" if ctx.L["observers"] else None)


EDGE_SUBJECTS = {
    "sensor-fusion>coop-tracking": lambda L: L["observers"],
}
