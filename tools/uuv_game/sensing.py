"""Sample the world once; publish measurements and belief, never hidden targets."""

import math

from . import observations as ekf


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


def observe(runtime):
    now = runtime.sim_time
    if now <= runtime.last_observation_time:
        return
    elapsed = max(0, now-max(0, runtime.last_observation_time))
    runtime.last_observation_time = now
    observed_modes = {boat["id"]: sensor_mode(runtime.active.get(boat["id"], {})) for boat in runtime.uuvs}
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
            sample = ekf.measure(boat["pose"], target["pose"], mode, runtime.rng)
            sample.update(observer_pose=list(boat["pose"]), observer_id=boat["id"], generation=boat["generation"],
                          observers=[boat["id"]], source="sensor")
            batch.append(sample)
        accepted = []
        for sample in batch:
            if not contact:
                x = sample["observer_pose"][0]+sample["range_m"]*math.cos(sample["bearing_rad"])
                y = sample["observer_pose"][1]+sample["range_m"]*math.sin(sample["bearing_rad"])
                runtime.contact_counter += 1
                key = f"CONTACT-{runtime.contact_counter}"
                runtime.contact_mapping[target["id"]] = key
                contact = {**ekf.initialize(x, y, now, max(15, sample["range_m"]*.04)),
                    "contact_id": key, "last_seen": now, "state": "tentative", "hits": [], "samples": [],
                    "vessel_class": target.get("vessel_class", "underwater"), "tracking_streak": 0, "observers": []}
                runtime.contacts[key] = contact
                runtime.event("contact_created", {"contact_id": key})
            if not ekf.correct(contact, sample):
                maximum_range = runtime.config.sensor_range
                if contact["state"] != "lost" or accepted or sample["mode"] != "active" or not 0 < sample["range_m"] <= maximum_range+4*sample["range_sigma"]:
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
            contact["samples"] = (contact["samples"]+[sample])[-20:]
        if not contact:
            continue
        old = contact["state"]
        if accepted:
            contact["last_seen"] = now
            contact["hits"] = [t for t in contact["hits"] if now-t <= 5]+[now]
            contact["observers"] = [s["observer_id"] for s in accepted]
        tracking = [u for u in runtime.uuvs if runtime.active.get(u["id"], {}).get("contact_id") == key
                    and runtime.active[u["id"]]["kind"] == "track"]
        active_received = {sample["observer_id"] for sample in accepted if sample["mode"] == "active" and sample["source"] == "sensor"}
        active_acquired = [boat for boat in tracking if boat["id"] in active_received
            and runtime.active[boat["id"]].get("phase") != "transit"]
        active_angles = [math.atan2(boat["pose"][1]-contact["y"], boat["pose"][0]-contact["x"]) for boat in active_acquired]
        active_geometry = max((abs(math.sin(a-b)) for a in active_angles for b in active_angles), default=0)
        if len(active_acquired) >= 2 and active_geometry >= .3 and contact["uncertainty_m"] <= 120 and any(
                runtime.active[boat["id"]].get("acquisition_mode") == "active" for boat in tracking):
            for boat in tracking:
                runtime.active[boat["id"]]["acquisition_mode"] = "passive"
                if runtime.active[boat["id"]].get("phase") == "reacquiring":
                    runtime.active[boat["id"]]["phase"] = "acquiring"
            runtime.event("tracking_passive_acquisition_started", {"contact_id": key,
                "members": [boat["id"] for boat in tracking], "observers": [boat["id"] for boat in active_acquired],
                "geometry_quality": active_geometry, "uncertainty_m": contact["uncertainty_m"]})
        received = {s["observer_id"] for s in accepted if s["mode"] == "passive"}
        acquired = [u for u in tracking if u["id"] in received and runtime.active[u["id"]].get("phase") != "transit"]
        angles = [math.atan2(u["pose"][1]-contact["y"], u["pose"][0]-contact["x"]) for u in acquired]
        geometry = max((abs(math.sin(a-b)) for a in angles for b in angles), default=0)
        effective = len(acquired) >= 2 and geometry >= .3 and contact["uncertainty_m"] <= 120
        contact["geometry_quality"] = geometry
        contact["tracking_streak"] = contact.get("tracking_streak", 0)+elapsed if effective else 0
        if now-contact["last_seen"] > 15:
            contact["state"] = "lost"
            runtime.metrics["lost_seconds"] += elapsed
        elif tracking:
            contact["state"] = "tracking" if contact["tracking_streak"] >= 3 else "degraded"
            if contact["state"] == "tracking":
                runtime.metrics["effective_tracking_seconds"] += elapsed
                for boat in acquired:
                    runtime.active[boat["id"]]["phase"] = "tracking"
        elif len(contact["hits"]) >= 3:
            contact["state"] = "confirmed"
        if old != contact["state"]:
            runtime.event("tracking_established" if contact["state"] == "tracking" else "target_lost" if contact["state"] == "lost" else "contact_state_changed",
                          {"contact_id": key, "state": contact["state"], "observers": contact["observers"]})
            if contact["state"] == "confirmed" and old in ("tentative", "lost"):
                runtime.event("target_found", {"contact_id": key})
                runtime.queue_agent(f"Contact {key} confirmed. Select a two-UUV tracking team, evaluate and submit its transit plan, preserving search coverage.", "target_found")
            elif contact["state"] == "lost":
                runtime.queue_agent(f"Contact {key} lost. Review active reacquisition and remaining coverage.", "target_lost")
    runtime.observations = runtime.observations[-500:]
    if not runtime.sensor_enabled:
        return
    for boat in runtime.uuvs:
        if observed_modes[boat["id"]] != "active" or "active" not in boat["capabilities"]:
            continue
        x, y = boat["pose"][:2]
        radius = runtime.config.sensor_range
        for col in range(max(0, int((x-radius)//100)), min(40, int((x+radius)//100)+1)):
            for row in range(max(0, int((4000-y-radius)//100)), min(40, int((4000-y+radius)//100)+1)):
                point = [(col+.5)*100, 4000-(row+.5)*100]
                if math.dist(point, [x, y]) <= radius and visible([x, y], point, runtime.obstacles):
                    runtime.scan_times[col][row] = now
