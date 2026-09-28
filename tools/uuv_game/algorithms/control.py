"""Bounded joint selection of simultaneous forward Dubins controls."""

import math

from ..config import algorithm_settings
from .motion import finite, integrate, positive, valid_pose
from .planning import path_safe

_CONTROL = algorithm_settings("control")

TIMES = tuple(_CONTROL["prediction_times_s"])


def _midpoint(left, right):
    angle = math.remainder(right[2]-left[2], 2*math.pi)
    scale = math.tan(angle/4)/2
    return [(left[0]+right[0])/2+(right[1]-left[1])*scale,
            (left[1]+right[1])/2-(right[0]-left[0])*scale,
            math.remainder(left[2]+angle/2, 2*math.pi)]


def _separated(left, right, clearance):
    if any(math.dist(a[:2], b[:2]) < clearance for a, b in zip(left, right)):
        return False
    intervals = [(a, b, c, d, 0) for a, b, c, d in zip(left, left[1:], right, right[1:])]
    while intervals:
        a, b, c, d, depth = intervals.pop()
        rx, ry = a[0]-c[0], a[1]-c[1]
        dx, dy = b[0]-d[0]-rx, b[1]-d[1]-ry
        square = dx*dx+dy*dy
        fraction = 0 if square == 0 else max(0, min(1, -(rx*dx+ry*dy)/square))
        padding = sum(math.dist(p[:2], q[:2])*math.tan(abs(math.remainder(q[2]-p[2], 2*math.pi))/4)/2 for p, q in ((a, b), (c, d)))
        if math.hypot(rx+fraction*dx, ry+fraction*dy) < clearance+padding:
            if depth >= _CONTROL["separation_subdivision_limit"]:
                return False
            middle_left, middle_right = _midpoint(a, b), _midpoint(c, d)
            if math.dist(middle_left[:2], middle_right[:2]) < clearance:
                return False
            intervals.extend(((a, middle_left, c, middle_right, depth+1), (middle_left, b, middle_right, d, depth+1)))
    return True


def choose_controls(boats, requests, contacts, obstacles, separation=_CONTROL["separation_m"],
                    speed=_CONTROL["speed_mps"], diagnostics=None):
    """Return id -> curvature, or None when the bounded joint search fails.

    Only requested boats move; unrequested own poses remain fixed. The fast
    path samples preferred controls only. A width-32 beam then compares at most
    15 controls per moving boat, checking shared predictions before any commit.
    """
    speed, separation = positive(speed, "speed"), positive(separation, "separation")
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.update(candidate_counts={}, reason=None, blocked_boat=None)
    if not isinstance(boats, list) or len(boats) > 8 or not isinstance(requests, dict):
        raise ValueError("joint control supports at most eight boats and keyed requests")
    poses = {boat["id"]: valid_pose(boat["pose"]) for boat in boats}
    if len(poses) != len(boats) or not set(requests) <= poses.keys():
        raise ValueError("control requests require unique known boat identifiers")
    previous = {boat["id"]: finite(boat.get("curvature", 0), "previous curvature") for boat in boats}
    turn_limit = 1/_CONTROL["turn_radius_m"]
    preferred = {identifier: max(-turn_limit, min(turn_limit, finite(request["preferred"], "preferred curvature"))) for identifier, request in requests.items()}
    fixed = {identifier: [pose[:]]*len(TIMES) for identifier, pose in poses.items() if identifier not in requests}
    estimates = contacts.values() if isinstance(contacts, dict) else contacts
    targets = []
    for contact in estimates:
        if contact.get("state") == "lost":
            continue
        x, y, vx, vy = [finite(contact[key], key) for key in ("x", "y", "vx", "vy")]
        uncertainty = max(0, finite(contact.get("uncertainty_m", 0), "uncertainty"))
        targets.append(([[x+vx*t, y+vy*t, 0] for t in TIMES], separation+min(_CONTROL["contact_uncertainty_clearance_m"], uncertainty),
                        max(_CONTROL["contact_uncertainty_buffer_min_m"], min(_CONTROL["contact_uncertainty_buffer_max_m"], uncertainty/2))))
    nearby = {identifier: {other for other in poses if other != identifier and math.dist(poses[identifier][:2], poses[other][:2]) <= 2*speed*TIMES[-1]+separation+4}
              for identifier in poses}

    def rollout(identifier, curvature, margin=_CONTROL["obstacle_margin_m"]):
        points = [integrate(poses[identifier], speed, curvature, t) for t in TIMES]
        if not path_safe(points, obstacles, requests[identifier]["execution_domain"], margin=margin):
            return None
        if any(not _separated(points, target, clearance) for target, clearance, _ in targets):
            return None
        if any(other in nearby[identifier] and not _separated(points, path, separation+_CONTROL["proximity_padding_m"]) for other, path in fixed.items()):
            return None
        return points

    def contact_risk(points):
        # Observation updates can move the hard envelope between ticks. Prefer
        # room beyond it, without rejecting an otherwise safe escape command.
        return sum(max(0, clearance+buffer-math.dist(point[:2], target[:2]))/buffer
                   for trajectory, clearance, buffer in targets for point, target in zip(points[1:], trajectory[1:]))

    best = {identifier: rollout(identifier, curvature) for identifier, curvature in preferred.items()}
    identifiers = sorted(best)
    if all(points is not None and contact_risk(points) == 0 for points in best.values()) and all(
        second not in nearby[first] or _separated(best[first], best[second], separation+_CONTROL["proximity_padding_m"])
        for i, first in enumerate(identifiers) for second in identifiers[i+1:]
    ):
        return preferred
    options = {}
    for identifier in identifiers:
        choices = sorted(set([preferred[identifier], max(-turn_limit, min(turn_limit, previous[identifier]))]+
            [n/_CONTROL["curvature_denominator"] for n in range(-_CONTROL["curvature_steps"], _CONTROL["curvature_steps"]+1)]))
        values = []
        for curvature in choices:
            points = best[identifier] if curvature == preferred[identifier] else rollout(identifier, curvature)
            if points is not None:
                cost = (abs(curvature-preferred[identifier])*_CONTROL["control_deviation_weight"]+
                        abs(curvature-previous[identifier])*_CONTROL["control_smoothness_weight"]+
                        _CONTROL["contact_risk_weight"]*contact_risk(points))
                values.append((cost, curvature, points))
        if not values:
            # A boat pressed against its domain edge can need the full boundary
            # to swing back inside; margin-zero rollouts are still required to
            # stay inside the domain, clear of obstacles and separated.
            for curvature in choices:
                points = rollout(identifier, curvature, margin=0)
                if points is not None:
                    cost = (abs(curvature-preferred[identifier])*_CONTROL["control_deviation_weight"]+
                            abs(curvature-previous[identifier])*_CONTROL["control_smoothness_weight"]+
                            _CONTROL["contact_risk_weight"]*contact_risk(points)+_CONTROL["boundary_recovery_weight"])
                    values.append((cost, curvature, points))
        if not values:
            diagnostics["candidate_counts"][identifier] = 0
            diagnostics.update(reason="no_static_safe_candidates", blocked_boat=identifier)
            return None
        options[identifier] = sorted(values, key=lambda value: value[:2])
        diagnostics["candidate_counts"][identifier] = len(values)
    # Unrelated boats must not consume the beam's alternatives for a close pair.
    # Displacement bounds prove different components cannot interact this horizon.
    pending, selected = set(identifiers), {}
    while pending:
        seed = min(pending)
        pending.remove(seed)
        component, frontier = {seed}, [seed]
        while frontier:
            neighbors = nearby[frontier.pop()] & pending
            pending.difference_update(neighbors)
            component.update(neighbors)
            frontier.extend(sorted(neighbors))
        beam = [(0.0, {}, {})]
        for identifier in sorted(component, key=lambda key: (len(options[key]), key)):
            expanded = []
            for cost, commands, predictions in beam:
                for extra, curvature, points in options[identifier]:
                    if any(other in nearby[identifier] and not _separated(points, path, separation+_CONTROL["proximity_padding_m"]) for other, path in predictions.items()):
                        continue
                    expanded.append((cost+extra, {**commands, identifier: curvature}, {**predictions, identifier: points}))
            if not expanded:
                diagnostics.update(reason="joint_beam_exhausted", blocked_boat=identifier, component=sorted(component))
                return None
            beam = sorted(expanded, key=lambda value: (value[0], tuple(sorted(value[1].items()))))[:_CONTROL["beam_width"]]
        selected.update(beam[0][1])
    return selected
