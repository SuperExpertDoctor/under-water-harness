"""Builtin plugin: task allocation (approved plan members -> UUVs).

Owns the plan-lifecycle pipeline stage.
"""

from .src import impl

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

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
STAGES = impl.STAGES
