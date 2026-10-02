"""Private noisy sensors and a bounded game-only evasion controller.

Only sample() sees simulator fleet poses. control() accepts the sensor product,
own pose and public map, never a runtime or opponent truth.
"""
import copy
import math
import random

from .algorithms.motion import integrate
from .algorithms.planning import path_safe
from .config import algorithm_settings
from .observations import measure
from .sensing import visible

_ADVERSARY = algorithm_settings("adversary")

def initial_state():
    return {"detections": [], "history": [], "mapping": {}, "parameters": None,
            "maneuver_history": [],
            "job": None, "last_started_s": -_ADVERSARY["observation_history_s"], "last_started_wall": 0.0,
            "last_heartbeat": 0.0, "status": "offline", "cycle": 0}


def sample(state, own_pose, opponents, obstacles, sensor_range, now, seed):
    rng = random.Random(seed)
    detections = []
    for opponent in opponents:
        if math.dist(own_pose[:2], opponent["pose"][:2]) > sensor_range or not visible(own_pose, opponent["pose"], obstacles):
            continue
        mapping = state["mapping"]
        if opponent["id"] not in mapping:
            mapping[opponent["id"]] = f"DETECTION-{len(mapping)+1}"
        detection = measure(own_pose, opponent["pose"], "active", rng)
        detection.update(contact_id=mapping[opponent["id"]], time_s=now,
                         observer_pose=list(own_pose))
        detections.append(detection)
    state["detections"] = detections
    state["history"] = [item for item in state["history"] if now-item["time_s"] <= _ADVERSARY["observation_history_s"]][-_ADVERSARY["maximum_history_entries"]:] + copy.deepcopy(detections)


def control(own_pose, detections, parameters, obstacles, config, now):
    """Expired parameters use 2.5 m/s, zero bias and close-contact evasion.

    A short swept-path rollout rejects boundary/obstacle collisions. If no safe
    forward arc exists, stopping is permitted; inference is never on this path.
    """
    active = parameters if parameters and parameters["expires_at_s"] > now else {}
    speed = max(0, min(config.enemy_max_speed, active.get("speed_mps", _ADVERSARY["fallback_speed_mps"])))
    if speed == 0:
        return list(own_pose), 0.0, 0.0
    bias = max(-1, min(1, active.get("turn_bias", 0)))
    x, y, heading = own_pose
    nearest = min(detections, key=lambda item: item["range_m"], default=None)
    if nearest and (nearest["range_m"] < _ADVERSARY["close_evasion_range_m"] or active):
        desired = nearest["bearing_rad"] + math.pi
    else:
        desired = heading + _ADVERSARY["fallback_turn_bias_rad"]
    if min(x, y, config.width-x, config.height-y) < _ADVERSARY["boundary_inset_m"]:
        desired = math.atan2(config.height/2-y, config.width/2-x)
    error = math.remainder(desired-heading, 2*math.pi)
    maximum = 1/config.enemy_turn_radius
    preferred = max(-maximum, min(maximum, error/_ADVERSARY["turn_response_m"] + bias*maximum*_ADVERSARY["turn_bias_gain"]))
    candidates = sorted({preferred, *(fraction*maximum for fraction in _ADVERSARY["curvature_candidates"])},
                        key=lambda k: abs(k-preferred))
    for curvature in candidates:
        points = [integrate(own_pose, speed, curvature, t if t else config.dt) for t in _ADVERSARY["control_horizon_s"]]
        if path_safe([own_pose, *points], obstacles, [0, 0, config.width, config.height], margin=_ADVERSARY["safety_margin_m"]):
            return integrate(own_pose, speed, curvature, config.dt), speed, curvature
    return list(own_pose), 0.0, 0.0
