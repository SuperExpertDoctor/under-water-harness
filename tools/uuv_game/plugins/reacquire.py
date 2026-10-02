"""Builtin plugin: lost-contact reacquisition (re-search -> redetect -> track).

Owns the stale-contact repair pipeline stage.
"""

from ..capabilities.lifecycle import repair_search
from ..config import algorithm_settings

_RUNTIME = algorithm_settings("runtime")

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


def stage_contact_repairs(rt):
    stale_contacts = {action["contact_id"] for action in rt.active.values()
        if action.get("kind") == "track" and not action.get("provisional") and action.get("contact_id") in rt.contacts
        and rt.contacts[action["contact_id"]]["state"] == "lost"
        and rt.sim_time-rt.contacts[action["contact_id"]]["last_seen"] >= _RUNTIME["reacquisition_max_unobserved_s"]
        and not any(other.get("kind") == "track" and other.get("contact_id") == action["contact_id"]
            and other.get("phase") == "transit" for other in rt.active.values())
        and all(rt.sim_time-other.get("acquisition_started_at_s", rt.contacts[action["contact_id"]]["last_seen"])
            >= _RUNTIME["reacquisition_max_unobserved_s"] for other in rt.active.values()
            if other.get("kind") == "track" and other.get("contact_id") == action["contact_id"])}
    for contact_id in stale_contacts:
        members = [member for member, action in rt.active.items()
            if action.get("kind") == "track" and action.get("contact_id") == contact_id]
        if not rt.standing_policy.get("local_repair"):
            rt.pause("safety_stale_contact_repair_authorization_required")
            return False
        for member in members:
            rt.active.pop(member)
        if repair_search(rt, add=members, required=False):
            rt.event("stale_contact_search_resumed", {"contact_id": contact_id, "members": members,
                "last_seen_s": rt.contacts[contact_id]["last_seen"]})
        rt.queue_agent(f"Contact {contact_id} has no fresh observation. Coverage search resumed; plan new tracking only after a measured reacquisition.", "target_lost")
    # Boats dropped by an infeasible repair (coverage_gap_accepted) would
    # otherwise idle forever; retry the repartition as positions evolve.
    pending = [member for member in rt.repair_pending if member not in rt.active]
    if pending and rt.standing_policy["local_repair"]:
        repair_search(rt, add=pending, required=False)
    rt.repair_pending.difference_update(rt.active)
    return True


STAGES = {"contact_repairs": stage_contact_repairs}
