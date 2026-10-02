"""Game sensors and a four-state CV EKF. No truth is accepted by the filter."""

import math

import numpy as np

from ..config import algorithm_settings

_OBS = algorithm_settings("observations")


def measure(observer, target, mode, rng):
    dx, dy = target[0]-observer[0], target[1]-observer[1]
    sigma = math.radians(_OBS["bearing_sigma_deg"])
    result = {"mode": mode, "bearing_rad": math.remainder(math.atan2(dy, dx)+rng.gauss(0, sigma), 2*math.pi),
              "bearing_sigma": sigma}
    if mode == "active":
        range_sigma = _OBS["active_range_sigma_m"]
        result.update(range_m=max(1, math.hypot(dx, dy)+rng.gauss(0, range_sigma)), range_sigma=range_sigma)
    return result


def initialize(x, y, now, sigma=_OBS["initial_contact_sigma_m"]):
    return {"x": float(x), "y": float(y), "vx": 0.0, "vy": 0.0,
            "covariance": np.diag([sigma*sigma, sigma*sigma, 9, 9]).tolist(),
            "estimate_time": now, "uncertainty_m": float(2*sigma)}


def initialize_active(observation, now):
    """Restart a lost estimate using only a measured active bearing and range."""
    if observation["mode"] != "active":
        raise ValueError("active_measurement_required")
    distance, bearing = observation["range_m"], observation["bearing_rad"]
    ox, oy = observation["observer_pose"][:2]
    cosine, sine = math.cos(bearing), math.sin(bearing)
    state = initialize(ox+distance*cosine, oy+distance*sine, now)
    transform = np.array([[cosine, -distance*sine], [sine, distance*cosine]])
    covariance = np.array(state["covariance"])
    covariance[:2, :2] = transform @ np.diag([observation["range_sigma"]**2, observation["bearing_sigma"]**2]) @ transform.T
    _store(state, np.array([state["x"], state["y"], 0.0, 0.0]), covariance)
    return state


def _store(contact, state, covariance):
    covariance = (covariance+covariance.T)*.5
    contact.update(zip(("x", "y", "vx", "vy"), state.tolist()))
    contact["covariance"] = covariance.tolist()
    contact["uncertainty_m"] = float(2*math.sqrt(max(0, np.linalg.eigvalsh(covariance[:2, :2])[-1])))


def predict(contact, now):
    dt = now-contact["estimate_time"]
    if dt <= 0:
        return
    state = np.array([contact[k] for k in ("x", "y", "vx", "vy")])
    transition = np.eye(4)
    transition[0, 2] = transition[1, 3] = dt
    gain = np.array([[dt*dt/2, 0], [0, dt*dt/2], [dt, 0], [0, dt]])
    covariance = transition @ np.array(contact["covariance"]) @ transition.T + gain @ gain.T * _OBS["process_acceleration_mps2"]**2
    _store(contact, transition @ state, covariance)
    contact["estimate_time"] = now


def correct(contact, observation):
    state = np.array([contact[k] for k in ("x", "y", "vx", "vy")])
    covariance = np.array(contact["covariance"])
    ox, oy = observation["observer_pose"][:2]
    dx, dy = state[0]-ox, state[1]-oy
    square = max(1, dx*dx+dy*dy)
    jacobian = np.array([[-dy/square, dx/square, 0, 0]])
    residual = np.array([math.remainder(observation["bearing_rad"]-math.atan2(dy, dx), 2*math.pi)])
    noise = np.array([[observation["bearing_sigma"]**2]])
    if observation["mode"] == "active":
        distance = math.sqrt(square)
        jacobian = np.vstack([jacobian, [dx/distance, dy/distance, 0, 0]])
        residual = np.append(residual, observation["range_m"]-distance)
        noise = np.diag([observation["bearing_sigma"]**2, observation["range_sigma"]**2])
    innovation = jacobian @ covariance @ jacobian.T + noise
    if float(residual @ np.linalg.solve(innovation, residual)) > _OBS["outlier_mahalanobis_squared"]:
        return False
    gain = np.linalg.solve(innovation, jacobian @ covariance).T
    identity = np.eye(4)-gain @ jacobian
    _store(contact, state+gain @ residual, identity @ covariance @ identity.T + gain @ noise @ gain.T)
    return True
