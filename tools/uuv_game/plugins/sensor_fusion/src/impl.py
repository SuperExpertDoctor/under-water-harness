"""Implementation of the sensor-fusion plugin — internal; the
public interface is re-exported by the package __init__.
"""

from ....capabilities.handover import finish_handover
from ....config import algorithm_settings

_RUNTIME = algorithm_settings("runtime")



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
