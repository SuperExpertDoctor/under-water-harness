"""Builtin plugin: dynamic task-region partitioning."""

from .src import impl

PLUGIN = {
    "id": "region-partition", "name": "动态任务区域划分", "layer": 1, "color": "#0369a1",
    "desc": "把搜索海域划为责任区，随能源与覆盖动态修补",
    "snippet": "按覆盖时效与目标线索动态重划责任区",
    "guidelines": ["执行中的责任区保持边界，只重划无属水域", "核心算法插件，不可关闭"],
    "inputs": "one", "outputs": "many", "core": True,
    "edges": [
        ("region-partition", "task-allocation"),
    ],
    "owns_stages": ["contact_repairs", "coverage_review"],
}

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
