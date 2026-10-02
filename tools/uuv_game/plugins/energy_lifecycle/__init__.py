"""Builtin plugin: energy lifecycle (fuel watch, exits, replacements).

Owns the exit-preparation pipeline stage.
"""

from .src import impl

PLUGIN = {
    "id": "energy-lifecycle", "name": "能源轮换", "layer": 0, "color": "#0f766e",
    "desc": "油量监视、低油量返航、替补艇生成与跟踪交接",
    "snippet": "监视能源并驱动低油量艇返航与替补轮换",
    "guidelines": ["退场艇在交接窗口内优先等待接替艇到位", "核心算法插件，不可关闭"],
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("energy-lifecycle", "task-allocation"),
        ("energy-lifecycle", "path-planning"),
        {"from": "energy-lifecycle", "to": "region-partition", "always_active": True},
    ],
    "owns_stages": ["exit_prep", "motion"],
}

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
STAGES = impl.STAGES
