"""Builtin plugin: cooperative target tracking (passive-forward sonar team).

Owns the provisional-lease and handover-preparation pipeline stages; the
stage functions run inside the runtime's tick under its synchronization.
"""

from .src import impl

PLUGIN = {
    "id": "coop-tracking", "name": "协同目标跟踪", "layer": 3, "color": "#be123c",
    "desc": "多艇前视被动声纳在协同区内编队稳定跟踪",
    "snippet": "多艇在协同区内按被动方位编队稳定跟踪",
    "guidelines": ["成员到位且几何达标后整队切换被动模式", "核心算法插件，不可关闭"],
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("coop-tracking", "reacquire"),
        ("coop-tracking", "uuv-control"),
    ],
    "owns_stages": ["track_leases", "handover_prep", "motion", "observations"],
}

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
STAGES = impl.STAGES
