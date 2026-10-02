"""Builtin plugin: multi-UUV bearing fusion (EKF contact estimation).

Owns the observation pipeline stage.
"""

from .src import impl

PLUGIN = {
    "id": "sensor-fusion", "name": "多艇方位融合", "layer": 2, "color": "#0e7490",
    "desc": "≥2 艇前视被动方位观测经 EKF 融合为目标估计",
    "snippet": "把多艇被动方位观测融合为目标估计",
    "guidelines": ["单艇方位只给方向线，稳定跟踪需要几何达标的双艇以上", "核心算法插件，不可关闭"],
    "inputs": "many", "outputs": "one", "core": True,
    "edges": [
        ("sensor-fusion", "coop-tracking"),
    ],
    "owns_stages": ["observations"],
}

activity = impl.activity
EDGE_SUBJECTS = impl.EDGE_SUBJECTS
STAGES = impl.STAGES
