"""Implementation of the task-allocation plugin — internal; the
public interface is re-exported by the package __init__.
"""



def activity(runtime, ctx):
    if ctx.L["allocated"]:
        for uid in ctx.L["allocated"]:
            ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['plans'])} 个活跃计划" if ctx.L["plans"] else None)


EDGE_SUBJECTS = {
    "task-allocation>coverage-search": lambda L: L["searching"],
    "task-allocation>path-planning": lambda L: L["transit_track"] + L["transit_other"] + L["exiting"],
    "task-allocation>coop-tracking": lambda L: L["tracking"],
    "task-allocation>reacquire": lambda L: L["reacquire_members"],
}


def stage_plan_lifecycle(rt):
    for plan in rt.plans.values():
        if plan["status"] == "pending_approval" and rt.sim_time > plan["expires_at_s"]:
            plan["status"] = "expired"
            rt._requeue_auto_track(plan)
            rt.event("approval_expired", {"plan_id": plan["plan_id"]})
    for intent in rt.intents:
        if intent["lifecycle"] == "active" and rt.sim_time/60 >= intent["expires_at_min"]:
            intent["lifecycle"] = "expired"
    return True


STAGES = {"plan_lifecycle": stage_plan_lifecycle}
