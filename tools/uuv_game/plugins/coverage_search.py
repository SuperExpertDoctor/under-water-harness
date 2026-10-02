"""Builtin plugin: area coverage search (side-scan + forward active sonar).

Owns the periodic coverage-review pipeline stage.
"""

from ..capabilities.lifecycle import repair_search
from ..config import algorithm_settings

_RUNTIME = algorithm_settings("runtime")

PLUGIN = {
    "id": "coverage-search", "name": "区域覆盖搜索", "layer": 3, "color": "#15803d",
    "desc": "侧扫 + 前视主动声纳沿责任区扫线持续覆盖",
    "snippet": "沿责任区扫线持续覆盖并积累扫描时效",
    "guidelines": ["每艇只持有一个责任区，单输入端子", "核心算法插件，不可关闭"],
    "inputs": "one", "outputs": "many", "core": True,
    "edges": [
        ("coverage-search", "coop-tracking"),
        ("coverage-search", "uuv-control"),
    ],
    "owns_stages": ["motion", "coverage_review"],
}


def activity(runtime, ctx):
    for uid in ctx.L["searching"]:
        ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['searching'])} 艇 · {len(ctx.L['owners'])} 责任区" if ctx.L["searching"] else None)


EDGE_SUBJECTS = {
    "coverage-search>coop-tracking": lambda L: [c["contact_id"] for c in L["contacts"]],
    "coverage-search>uuv-control": lambda L: L["moving"],
}


def stage_coverage_review(rt):
    if rt.sim_time-rt.last_periodic >= _RUNTIME["periodic_review_s"]:
        rt.last_periodic = rt.sim_time
        if rt._search_gap():
            rt.queue_agent("Coverage gap or overdue revisit detected; preserve valid plans.", "coverage_gap")
    # Region routes sweep only the cells that were due when the last
    # partition ran; owned cells that expire inside the coverage window
    # would otherwise wait for an external trigger and silently decay.
    # A throttled local repair refreshes the due set and its routes.
    if (rt.standing_policy["local_repair"]
            and rt.sim_time-rt.last_coverage_replan >= _RUNTIME["coverage_replan_s"]):
        rt.last_coverage_replan = rt.sim_time
        window_s = rt.config.coverage_window_min*60
        due = any(0 <= cell[0] < len(rt.scan_times) and 0 <= cell[1] < len(rt.scan_times[cell[0]])
                  and (rt.scan_times[cell[0]][cell[1]] < 0
                       or rt.sim_time-rt.scan_times[cell[0]][cell[1]] > window_s)
                  for region in rt.regions for cell in region["cells"])
        if due:
            repair_search(rt, required=False)
    return True


STAGES = {"coverage_review": stage_coverage_review}
