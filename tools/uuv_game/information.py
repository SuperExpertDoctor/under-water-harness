"""Stateless display fields from scan history and contact belief, never target truth."""

import copy
import math

import numpy as np

from .observations import predict


def information_fields(scan_times, contacts, searchable, now, config):
    scans = np.asarray(scan_times, dtype=float)
    mask = np.zeros(scans.shape, dtype=bool)
    for col, row in searchable:
        mask[col, row] = True
    freshness = np.zeros(scans.shape)
    valid = mask & np.isfinite(scans) & (scans >= 0)
    freshness[valid] = np.exp(-math.log(2)*np.maximum(0, now-scans[valid])/config.scan_half_life_s)
    evidence = np.zeros(scans.shape)
    columns, rows = np.indices(scans.shape)
    x, y = (columns+.5)*100, 4000-(rows+.5)*100
    for contact in contacts.values():
        samples = [sample for sample in contact.get("samples", [])
                   if sample.get("source") == "sensor" and math.isfinite(sample.get("time_s", -1))
                   and 0 <= sample.get("time_s", -1) <= now]
        localized = contact.get("position_localized", False) or any(
            sample.get("mode") == "active" and sample.get("range_m", 0) > 0 for sample in samples)
        if localized:
            last_seen = contact.get("last_seen", -1)
            if not math.isfinite(last_seen) or last_seen < 0:
                continue
            belief = copy.deepcopy(contact)
            covariance = np.asarray(belief.get("covariance", []), dtype=float)
            if covariance.shape != (4, 4) or not np.isfinite(covariance).all():
                continue
            if not all(math.isfinite(belief.get(key, math.nan)) for key in ("x", "y", "vx", "vy", "estimate_time")):
                continue
            predict(belief, now)
            covariance = np.asarray(belief["covariance"])[:2, :2]
            eigenvalues, orientation = np.linalg.eigh(covariance)
            # The display neighborhood has a 150 m floor; growing uncertainty
            # spreads the same evidence and reduces its peak by covariance area.
            eigenvalues = np.maximum(eigenvalues, 150.0**2)
            covariance = (orientation*eigenvalues) @ orientation.T
            delta = np.stack((x-belief["x"], y-belief["y"]), axis=-1)
            distance = np.einsum("...i,ij,...j->...", delta, np.linalg.inv(covariance), delta)
            confidence = contact.get("confidence", .45 if contact.get("state") == "tentative" else .85)
            if not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
                continue
            amplitude = min(1, max(0, confidence))*math.exp(-math.log(2)*max(0, now-last_seen)/config.target_half_life_s)
            amplitude *= 150.0**2/math.sqrt(float(np.prod(eigenvalues)))
            field = np.where(distance <= 9, amplitude*np.exp(-.5*distance), 0)
            evidence = np.maximum(evidence, field)
        else:
            for sample in samples:
                if sample.get("mode") != "passive":
                    continue
                ox, oy = sample["observer_pose"][:2]
                dx, dy = x-ox, y-oy
                distance = np.hypot(dx, dy)
                angle = np.arctan2(dy, dx)-sample["bearing_rad"]
                angle = (angle+math.pi) % (2*math.pi)-math.pi
                # Include a cell's angular footprint without inventing a range.
                sigma = max(sample["bearing_sigma"], math.radians(2))
                width = sigma+np.arctan2(50, np.maximum(1, distance))
                amplitude = .3*math.exp(-math.log(2)*(now-sample["time_s"])/config.target_half_life_s)
                field = np.where((distance > 0) & (distance <= config.sensor_range) & (np.abs(angle) <= 3*width),
                                 amplitude*np.exp(-.5*(angle/width)**2), 0)
                evidence = np.maximum(evidence, field)
    evidence[~mask] = 0
    return {"info_matrix": freshness.tolist(), "target_info_matrix": np.clip(evidence, 0, 1).tolist(),
            "information_model": {"schema": "uuv-information/v1", "scan_half_life_s": config.scan_half_life_s,
                                  "target_half_life_s": config.target_half_life_s, "as_of_s": now}}
