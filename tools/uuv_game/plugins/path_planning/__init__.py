"""Builtin plugin: cooperative path planning (virtual-leader trajectories)."""

from .src import impl

PLUGIN = {
    "id": "path-planning", "name": "协同路径规划", "layer": 2, "color": "#4338ca",
    "desc": "虚拟领航点轨迹：引导 UUV 到站位、责任区或出口",
    "snippet": "以虚拟领航点轨迹引导 UUV 到站位或区域",
    "guidelines": ["转场途中目标进入主动窄波束仍会采样补盲", "核心算法插件，不可关闭"],
    "inputs": "many", "outputs": "many", "core": True,
    "edges": [
        ("path-planning", "coverage-search"),
        ("path-planning", "coop-tracking"),
        ("path-planning", "uuv-control"),
    ],
    "owns_stages": ["motion"],
}

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
