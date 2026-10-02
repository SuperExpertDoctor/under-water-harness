"""Implementation of the coop-tracking plugin — internal; the
public interface is re-exported by the package __init__.
"""

from ....capabilities.handover import prepare_handover
from ....config import algorithm_settings

_RUNTIME = algorithm_settings("runtime")



def activity(runtime, ctx):
    for uid in ctx.L["tracking"]:
        ctx.hit(uid)
    tracked = ctx.L["tracked"]
    ctx.meta("/".join(c["contact_id"] for c in tracked) + f" {tracked[0]['state']}" if tracked else None)


EDGE_SUBJECTS = {
    "coop-tracking>reacquire": lambda L: [c["contact_id"] for c in L["lost"]],
    "coop-tracking>uuv-control": lambda L: L["moving"],
}


def stage_track_leases(rt):
    for member, action in list(rt.active.items()):
        if action.get("provisional") and rt.sim_time >= action["expires_at_s"]:
            if not rt._end_provisional_contact(member, "lease_expired"):
                return False
    return True


def stage_handover_prep(rt):
    if rt.frame_id % _RUNTIME["safety_review_frames"] == 0:
        prepare_handover(rt)
    return True


STAGES = {"track_leases": stage_track_leases, "handover_prep": stage_handover_prep}
