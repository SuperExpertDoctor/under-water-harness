"""Builtin plugin: multi-UUV bearing fusion (EKF contact estimation).

Owns the observation pipeline stage.
"""

from ..capabilities.handover import finish_handover
from ..config import algorithm_settings

_RUNTIME = algorithm_settings("runtime")

PLUGIN = {
    "id": "sensor-fusion", "name": "多艇方位融合", "layer": 2, "color": "#0e7490",
    "desc": "≥2 艇前视被动方位观测经 EKF 融合为目标估计",
    "snippet": "把多艇被动方位观测融合为目标估计",
    "guidelines": ["单艇方位只给方向线，稳定跟踪需要几何达标的双艇以上", "核心算法插件，不可关闭"],
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


def stage_observations(rt):
    if rt.frame_id % _RUNTIME["observation_frames"] == 0:
        rt._observe()
        finish_handover(rt)
        for key, contact in rt.contacts.items():
            if contact.get("auto_track_requested"):
                rt._auto_track(key)
        for u in rt.uuvs:
            u["trail"] = (u["trail"]+[u["pose"][:2]])[-_RUNTIME["max_trail_points"]:]
    return True


STAGES = {"observations": stage_observations}
