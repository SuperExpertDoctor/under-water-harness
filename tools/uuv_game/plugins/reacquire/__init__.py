"""Builtin plugin: lost-contact reacquisition (re-search -> redetect -> track).

Owns the stale-contact repair pipeline stage.
"""

from .src import impl

PLUGIN = {
    "id": "reacquire", "name": "失联再捕获", "layer": 3, "color": "#a16207",
    "desc": "目标丢失后重搜该区域、再发现并恢复跟踪",
    "snippet": "目标丢失后重搜区域、再发现并恢复跟踪",
    "guidelines": ["再捕获搜索不占用跟踪计划名额", "核心算法插件，不可关闭"],
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("reacquire", "coop-tracking"),
        ("reacquire", "uuv-control"),
    ],
    "owns_stages": ["contact_repairs", "motion"],
}

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
STAGES = impl.STAGES
