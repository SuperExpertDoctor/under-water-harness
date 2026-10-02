"""Builtin plugin: area coverage search (side-scan + forward active sonar).

Owns the periodic coverage-review pipeline stage.
"""

from .src import impl

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

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
STAGES = impl.STAGES
