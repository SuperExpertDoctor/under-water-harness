"""Stateless display fields from scan history and contact belief, never target truth."""

import copy
import math

import numpy as np

from ..config import algorithm_settings
from .observations import predict

_INFORMATION = algorithm_settings("information")

def target_evidence_field(scan_times, contacts, now, config, excluded_contact_ids=()):
    """Search-relevant target evidence for planning, over every ledger cell."""
    excluded = set(excluded_contact_ids)
    relevant = {contact_id: contact for contact_id, contact in contacts.items() if contact_id not in excluded}
    cells = [(col, row) for col in range(len(scan_times)) for row in range(len(scan_times[col]))]
    return information_fields(scan_times, relevant, cells, now, config)["target_info_matrix"]


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
    x, y = (columns+.5)*config.cell, config.height-(rows+.5)*config.cell
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
            floor = _INFORMATION["target_floor_radius_m"]
            eigenvalues = np.maximum(eigenvalues, floor**2)
            covariance = (orientation*eigenvalues) @ orientation.T
            delta = np.stack((x-belief["x"], y-belief["y"]), axis=-1)
            distance = np.einsum("...i,ij,...j->...", delta, np.linalg.inv(covariance), delta)
            confidence = contact.get("confidence", _INFORMATION["tentative_confidence"] if contact.get("state") == "tentative" else _INFORMATION["confirmed_confidence"])
            if not isinstance(confidence, (int, float)) or not math.isfinite(confidence):
                continue
            amplitude = min(1, max(0, confidence))*math.exp(-math.log(2)*max(0, now-last_seen)/config.target_half_life_s)
            amplitude *= floor**2/math.sqrt(float(np.prod(eigenvalues)))
            field = np.where(distance <= _INFORMATION["local_evidence_sigma_squared"], amplitude*np.exp(-.5*distance), 0)
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
                sigma = max(sample["bearing_sigma"], math.radians(_INFORMATION["bearing_sigma_floor_deg"]))
                width = sigma+np.arctan2(config.cell/2, np.maximum(1, distance))
                amplitude = _INFORMATION["bearing_evidence_gain"]*math.exp(-math.log(2)*(now-sample["time_s"])/config.target_half_life_s)
                field = np.where((distance > 0) & (distance <= config.sensor_range) & (np.abs(angle) <= _INFORMATION["local_evidence_sigma_limit"]*width),
                                 amplitude*np.exp(-.5*(angle/width)**2), 0)
                evidence = np.maximum(evidence, field)
    evidence[~mask] = 0
    observed = freshness[mask]
    return {"info_matrix": freshness.tolist(), "target_info_matrix": np.clip(evidence, 0, 1).tolist(),
            "information_cell_counts": {"white": int(np.count_nonzero(observed > .7)),
                                        "gray": int(np.count_nonzero((observed >= .2) & (observed <= .7))),
                                        "black": int(np.count_nonzero(observed < .2))},
            "information_model": {"schema": "uuv-information/v1", "scan_half_life_s": config.scan_half_life_s,
                                  "target_half_life_s": config.target_half_life_s, "as_of_s": now}}
