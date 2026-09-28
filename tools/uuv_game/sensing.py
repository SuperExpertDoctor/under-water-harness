"""Sample the world once; publish measurements and belief, never hidden targets."""

import math

from . import observations as ekf
from .config import algorithm_settings

_OBS = algorithm_settings("observations")


def visible(left, right, obstacles):
    dx, dy = right[0]-left[0], right[1]-left[1]
    square = max(1, dx*dx+dy*dy)
    for obstacle in obstacles:
        t = max(0, min(1, ((obstacle["x"]-left[0])*dx+(obstacle["y"]-left[1])*dy)/square))
        if math.hypot(left[0]+t*dx-obstacle["x"], left[1]+t*dy-obstacle["y"]) <= obstacle["radius"]:
            return False
    return True


def sensor_mode(action):
    if action.get("kind") in ("search", "reacquire") or action.get("phase") == "reacquiring" or action.get("acquisition_mode") == "active":
        return "active"
    return "passive" if action.get("kind") == "track" else "off"


def sensor_roles(action):
    kind, mode = action.get("kind"), sensor_mode(action)
    return {"side_scan": kind == "search", "forward_active": bool(kind and kind != "idle"),
            "forward_passive": mode == "passive"}


def _relative_bearing(pose, point):
    return abs(math.remainder(math.atan2(point[1]-pose[1], point[0]-pose[0])-pose[2], 2*math.pi))


def side_scan_contains(pose, point, config):
    distance = math.dist(pose[:2], point[:2])
    angle = _relative_bearing(pose, point)
    return (config.side_scan_inner_range <= distance <= config.sensor_range and
            abs(angle-math.pi/2) <= math.radians(config.side_scan_half_angle_deg))


def forward_contains(pose, point, config, passive=False):
    angle = config.forward_passive_half_angle_deg if passive else config.forward_active_half_angle_deg
    return math.dist(pose[:2], point[:2]) <= config.sensor_range and _relative_bearing(pose, point) <= math.radians(angle)


def observe(runtime):
    now = runtime.sim_time
    if now <= runtime.last_observation_time:
        return
    elapsed = max(0, now-max(0, runtime.last_observation_time))
    runtime.last_observation_time = now
    observed_modes = {boat["id"]: sensor_mode(runtime.active.get(boat["id"], {})) for boat in runtime.uuvs}
    observed_roles = {boat["id"]: sensor_roles(runtime.active.get(boat["id"], {})) for boat in runtime.uuvs}
    runtime.obstacle_observations = [
        {"observer_id": boat["id"], "obstacle_id": f"obstacle-{index}", "time_s": now}
        for boat in runtime.uuvs if runtime.sensor_enabled and observed_roles[boat["id"]]["forward_active"]
        for index, obstacle in enumerate(runtime.obstacles)
        if forward_contains(boat["pose"], [obstacle["x"], obstacle["y"]], runtime.config)]
    for contact in runtime.contacts.values():
        ekf.predict(contact, now)
    for target in runtime.targets:
        key = runtime.contact_mapping.get(target["id"])
        contact = runtime.contacts.get(key)
        batch = []
        observers = runtime.uuvs if runtime.sensor_enabled else []
        for boat in observers:
            mode = observed_modes[boat["id"]]
            if mode == "off" or mode not in boat["capabilities"]:
                continue
            if mode == "passive" and (not contact or not target.get("passive_signal", True)):
                continue
            if math.dist(boat["pose"][:2], target["pose"][:2]) > runtime.config.sensor_range:
                continue
            if not visible(boat["pose"], target["pose"], runtime.obstacles):
                continue
            roles = observed_roles[boat["id"]]
            side_contact = roles["side_scan"] and side_scan_contains(boat["pose"], target["pose"], runtime.config)
            front_contact = (roles["forward_passive"] if mode == "passive" else roles["forward_active"]) and forward_contains(
                boat["pose"], target["pose"], runtime.config, passive=mode == "passive")
            if not (side_contact or front_contact):
                continue
            sample = ekf.measure(boat["pose"], target["pose"], mode, runtime.rng)
            sample.update(observer_pose=list(boat["pose"]), observer_id=boat["id"], generation=boat["generation"],
                          observers=[boat["id"]], source="sensor", instrument="side_scan" if side_contact else "forward_passive" if mode == "passive" else "forward_active")
            batch.append(sample)
        accepted = []
        for sample in batch:
            if not contact:
                x = sample["observer_pose"][0]+sample["range_m"]*math.cos(sample["bearing_rad"])
                y = sample["observer_pose"][1]+sample["range_m"]*math.sin(sample["bearing_rad"])
                runtime.contact_counter += 1
                key = f"CONTACT-{runtime.contact_counter}"
                runtime.contact_mapping[target["id"]] = key
                contact = {**ekf.initialize(x, y, now, max(_OBS["initial_contact_sigma_m"], sample["range_m"]*_OBS["initial_contact_range_sigma_fraction"])),
                    "contact_id": key, "last_seen": now, "state": "tentative", "hits": [], "samples": [],
                    "vessel_class": target.get("vessel_class", "underwater"), "tracking_streak": 0, "observers": []}
                runtime.contacts[key] = contact
                runtime.event("contact_created", {"contact_id": key})
            if not ekf.correct(contact, sample):
                maximum_range = runtime.config.sensor_range
                if contact["state"] != "lost" or accepted or sample["mode"] != "active" or not 0 < sample["range_m"] <= maximum_range+_OBS["active_reacquire_sigma_multiple"]*sample["range_sigma"]:
                    continue
                contact.update(ekf.initialize_active(sample, now), tracking_streak=0)
                runtime.event("contact_reacquired", {"contact_id": key, "observer_id": sample["observer_id"],
                    "generation": sample["generation"], "method": "active_measurement_reinitialization"})
            runtime.observation_cursor += 1
            sample.update(sample_id=f"obs-{runtime.observation_cursor}", sequence=runtime.observation_cursor,
                          contact_id=key, time_s=now)
            if sample["mode"] == "active":
                contact["position_localized"] = True
                sample.update(x=sample["observer_pose"][0]+sample["range_m"]*math.cos(sample["bearing_rad"]),
                              y=sample["observer_pose"][1]+sample["range_m"]*math.sin(sample["bearing_rad"]))
            runtime.observations.append(sample)
            accepted.append(sample)
            contact["samples"] = (contact["samples"]+[sample])[-_OBS["contact_sample_history"]:]
        if not contact:
            continue
        old = contact["state"]
        if accepted:
            contact["last_seen"] = now
            contact["hits"] = [t for t in contact["hits"] if now-t <= _OBS["confirmation_window_s"]]+[now]
            contact["observers"] = [s["observer_id"] for s in accepted]
        tracking = [u for u in runtime.uuvs if runtime.active.get(u["id"], {}).get("contact_id") == key
                    and runtime.active[u["id"]]["kind"] == "track"]
        active_received = {sample["observer_id"] for sample in accepted if sample["mode"] == "active" and sample["source"] == "sensor"}
        active_acquired = [boat for boat in tracking if boat["id"] in active_received
            and runtime.active[boat["id"]].get("phase") != "transit"]
        active_angles = [math.atan2(boat["pose"][1]-contact["y"], boat["pose"][0]-contact["x"]) for boat in active_acquired]
        active_geometry = max((abs(math.sin(a-b)) for a in active_angles for b in active_angles), default=0)
        if len(active_acquired) >= 2 and active_geometry >= _OBS["tracking_geometry_min"] and contact["uncertainty_m"] <= _OBS["tracking_uncertainty_max_m"] and any(
                runtime.active[boat["id"]].get("acquisition_mode") == "active" for boat in tracking):
            for boat in tracking:
                runtime.active[boat["id"]]["acquisition_mode"] = "passive"
                runtime.active[boat["id"]]["established_once"] = True
                if runtime.active[boat["id"]].get("phase") == "reacquiring":
                    runtime.active[boat["id"]]["phase"] = "acquiring"
            runtime.event("tracking_passive_acquisition_started", {"contact_id": key,
                "members": [boat["id"] for boat in tracking], "observers": [boat["id"] for boat in active_acquired],
                "geometry_quality": active_geometry, "uncertainty_m": contact["uncertainty_m"]})
        received = {s["observer_id"] for s in accepted if s["mode"] == "passive"}
        acquired = [u for u in tracking if u["id"] in received and runtime.active[u["id"]].get("phase") != "transit"]
        angles = [math.atan2(u["pose"][1]-contact["y"], u["pose"][0]-contact["x"]) for u in acquired]
        geometry = max((abs(math.sin(a-b)) for a in angles for b in angles), default=0)
        effective = len(acquired) >= 2 and geometry >= _OBS["tracking_geometry_min"] and contact["uncertainty_m"] <= _OBS["tracking_uncertainty_max_m"]
        contact["geometry_quality"] = geometry
        contact["tracking_streak"] = contact.get("tracking_streak", 0)+elapsed if effective else 0
        if now-contact["last_seen"] > _OBS["contact_lost_after_s"]:
            contact["state"] = "lost"
            runtime.metrics["lost_seconds"] += elapsed
        elif tracking:
            contact["state"] = "tracking" if contact["tracking_streak"] >= _OBS["tracking_streak_s"] else "degraded"
            if contact["state"] == "tracking":
                runtime.metrics["effective_tracking_seconds"] += elapsed
                for boat in acquired:
                    runtime.active[boat["id"]]["phase"] = "tracking"
        elif len(contact["hits"]) >= _OBS["contact_confirmation_hits"]:
            contact["state"] = "confirmed"
        if old != contact["state"]:
            runtime.event("tracking_established" if contact["state"] == "tracking" else "target_lost" if contact["state"] == "lost" else "contact_state_changed",
                          {"contact_id": key, "state": contact["state"], "observers": contact["observers"]})
            if contact["state"] == "confirmed" and old in ("tentative", "lost"):
                runtime.event("target_found", {"contact_id": key})
                if old == "tentative" and hasattr(runtime, "provisional_contact"):
                    discoverer = next((sample for sample in accepted if sample["mode"] == "active"
                        and runtime.active.get(sample["observer_id"], {}).get("kind") == "search"), None)
                    if discoverer:
                        runtime.provisional_contact(key, discoverer)
                runtime.queue_agent(f"Contact {key} confirmed. Select a two-UUV tracking team, evaluate and submit its transit plan, preserving search coverage.", "target_found")
            elif contact["state"] == "lost":
                runtime.queue_agent(f"Contact {key} lost. Review active reacquisition and remaining coverage.", "target_lost")
    runtime.observations = runtime.observations[-_OBS["maximum_observations"]:]
    if not runtime.sensor_enabled:
        return
    for boat in runtime.uuvs:
        if not observed_roles[boat["id"]]["side_scan"] or "active" not in boat["capabilities"]:
            continue
        x, y = boat["pose"][:2]
        radius = runtime.config.sensor_range
        cell = runtime.config.cell
        for col in range(max(0, int((x-radius)//cell)), min(len(runtime.scan_times), int((x+radius)//cell)+1)):
            for row in range(max(0, int((runtime.config.height-y-radius)//cell)), min(len(runtime.scan_times[col]), int((runtime.config.height-y+radius)//cell)+1)):
                point = [(col+.5)*cell, runtime.config.height-(row+.5)*cell]
                if side_scan_contains(boat["pose"], point, runtime.config) and visible([x, y], point, runtime.obstacles):
                    runtime.scan_times[col][row] = now
