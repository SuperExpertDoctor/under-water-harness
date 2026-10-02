"""Builtin plugin: cooperative target tracking (passive-forward sonar team).

Owns the provisional-lease and handover-preparation pipeline stages; the
stage functions run inside the runtime's tick under its synchronization.
"""

from ..capabilities.handover import prepare_handover
from ..config import algorithm_settings

_RUNTIME = algorithm_settings("runtime")

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
