"""Private noisy sensors and a bounded game-only evasion controller.

Only sample() sees simulator fleet poses. control() accepts the sensor product,
own pose and public map, never a runtime or opponent truth.
"""
import copy
import math
import random

from .algorithms.motion import integrate
from .algorithms.planning import path_safe
from .observations import measure
from .sensing import visible


def initial_state():
    return {"detections": [], "history": [], "mapping": {}, "parameters": None,
            "job": None, "last_started_s": -30.0, "last_started_wall": 0.0,
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
    state["history"] = [item for item in state["history"] if now-item["time_s"] <= 30][-152:] + copy.deepcopy(detections)


def control(own_pose, detections, parameters, obstacles, config, now):
    """Expired parameters use 2.5 m/s, zero bias and close-contact evasion.

    A short swept-path rollout rejects boundary/obstacle collisions. If no safe
    forward arc exists, stopping is permitted; inference is never on this path.
    """
    active = parameters if parameters and parameters["expires_at_s"] > now else {}
    speed = max(0, min(config.enemy_max_speed, active.get("speed_mps", 2.5)))
    bias = max(-1, min(1, active.get("turn_bias", 0)))
    x, y, heading = own_pose
    nearest = min(detections, key=lambda item: item["range_m"], default=None)
    if nearest and (nearest["range_m"] < 80 or active):
        desired = nearest["bearing_rad"] + math.pi
    else:
        desired = heading + .003
    if min(x, y, config.width-x, config.height-y) < 400:
        desired = math.atan2(config.height/2-y, config.width/2-x)
    error = math.remainder(desired-heading, 2*math.pi)
    maximum = 1/config.enemy_turn_radius
    preferred = max(-maximum, min(maximum, error/100 + bias*maximum*.35))
    candidates = sorted({preferred, -maximum, maximum, 0.0, -maximum/2, maximum/2}, key=lambda k: abs(k-preferred))
    for curvature in candidates:
        points = [integrate(own_pose, speed, curvature, t) for t in (0, config.dt, 1, 3, 6, 12)]
        if path_safe(points, obstacles, [0, 0, config.width, config.height], margin=20):
            return integrate(own_pose, speed, curvature, config.dt), speed, curvature
    return list(own_pose), 0.0, 0.0
