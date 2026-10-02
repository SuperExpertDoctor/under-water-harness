"""Builtin plugin: UUV platform control (heading/speed/sensor telemetry)."""

PLUGIN = {
    "id": "uuv-control", "name": "UUV 平台控制", "layer": 4, "color": "#475569",
    "desc": "航向 / 速度 / 传感器模式执行与平台遥测",
    "inputs": "many", "outputs": "one", "core": True,
    "edges": [],
    "owns_stages": ["motion"],
}


def activity(runtime, ctx):
    for uid in ctx.L["moving"]:
        ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['moving'])} 艇受控" if ctx.L["moving"] else None)


EDGE_SUBJECTS = {}
