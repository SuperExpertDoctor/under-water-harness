"""Builtin plugin: task allocation (approved plan members -> UUVs).

Owns the plan-lifecycle pipeline stage.
"""

PLUGIN = {
    "id": "task-allocation", "name": "任务分配", "layer": 1, "color": "#1d4ed8",
    "desc": "把获批计划的成员 / 责任区指派到具体 UUV",
    "snippet": "把计划成员与责任区落实到具体 UUV",
    "guidelines": ["候选分配不产生运动，需经评估并提交后才执行", "核心算法插件，不可关闭"],
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("task-allocation", "coverage-search"),
        ("task-allocation", "path-planning"),
        ("task-allocation", "coop-tracking"),
        ("task-allocation", "reacquire"),
    ],
    "owns_stages": ["plan_lifecycle"],
}


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
