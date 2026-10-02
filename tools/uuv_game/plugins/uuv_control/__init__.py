"""Builtin plugin: UUV platform control (heading/speed/sensor telemetry).

Owns the motion pipeline stage: per-UUV preferred controls, joint Dubins
validation, adversary stepping, pose integration, boundary turnover.
"""

from .src import impl

PLUGIN = {
    "id": "uuv-control", "name": "UUV 平台控制", "layer": 4, "color": "#475569",
    "desc": "航向 / 速度 / 传感器模式执行与平台遥测",
    "snippet": "执行航向、速度与传感器模式的平台指令",
    "guidelines": ["所有插件的运动意图最终经此输出到平台", "核心算法插件，不可关闭"],
    "inputs": "many", "outputs": "one", "core": True,
    "edges": [],
    "owns_stages": ["motion"],
}

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
STAGES = impl.STAGES
