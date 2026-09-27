"""Bounded geometric pursuit and moving-center distance-band orbit control."""

import math
from itertools import combinations, product

from .control import _separated
from .motion import finite, positive, valid_pose
from .planning import plan_path


def tracking_plan(uuvs, contact, obstacles, *, require_active_acquisition=True):
    """Enumerate bounded two/three-boat geometry using actual Dubins transits.

    Each slot is an arrival reference around a predicted moving contact, never
    a stationary control target. Larger input fleets trigger team selection.
    """
    if not isinstance(uuvs, list) or not 1 <= len(uuvs) <= 8:
        raise ValueError("tracking requires one to eight vehicles")
    identifiers = [u.get("id") for u in uuvs]
    if any(not isinstance(identifier, str) or not identifier for identifier in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("tracking identifiers must be unique nonempty strings")
    tx, ty, vx, vy = [finite(contact[key], key) for key in ("x", "y", "vx", "vy")]
    uncertainty = finite(contact.get("uncertainty_m", 0), "uncertainty_m")
    if uncertainty < 0:
        raise ValueError("uncertainty must not be negative")
    covariance = contact.get("covariance")
    if covariance is not None:
        if len(covariance) != 4 or any(len(row) != 4 for row in covariance):
            raise ValueError("contact covariance must be four by four")
        covariance = [[finite(value, "covariance") for value in row] for row in covariance]
    result = {"status": "infeasible", "members": [], "routes": {}, "slots": {}, "diagnostics": {
        "candidate_routes": 0, "candidate_teams": 0, "geometry_quality": 0.0,
        "reason": "insufficient feasible cooperative observation candidates"}}
    active_members = {boat["id"] for boat in uuvs if "active" in boat.get("capabilities", ["active", "passive"])}
    result.update(acquisition_mode="active_until_cooperative_geometry" if require_active_acquisition else "passive_existing_team",
        acquisition_requirements={"min_active_observers": 2 if require_active_acquisition else 0, "eligible_members": sorted(active_members)})
    if require_active_acquisition and len(active_members) < 2:
        result["diagnostics"]["reason"] = "active_acquisition_requires_two_active_sensors"
        return result
    feasible = {}
    for boat in uuvs:
        start = valid_pose(boat["pose"])
        capabilities = boat.get("capabilities", ["active", "passive"])
        if not any(capability in capabilities for capability in ("passive", "tracking", "track")):
            continue
        remaining = boat.get("remaining_range_m", math.inf)
        if remaining < 3000 or uncertainty >= 170:
            continue
        options = []
        for index in range(8):
            angle = index*math.pi/4
            best = None
            for distance in (220.0, 280.0):
                if distance+uncertainty > 350:
                    continue
                eta = math.dist(start[:2], [tx, ty])/4
                route = None
                for _ in range(3):
                    center = [tx+vx*eta, ty+vy*eta]
                    heading = math.atan2(vy+4*math.cos(angle), vx-4*math.sin(angle))
                    goal = [center[0]+distance*math.cos(angle), center[1]+distance*math.sin(angle), heading]
                    route = plan_path(start, goal, obstacles=obstacles, budget=1)
                    result["diagnostics"]["candidate_routes"] += 1
                    if route["status"] != "succeeded":
                        break
                    eta = route["length_m"]/4
                if route["status"] != "succeeded":
                    continue
                predicted = [tx+vx*eta, ty+vy*eta]
                actual_distance = math.dist(goal[:2], predicted)
                exit_reserve = min(goal[0], goal[1], 4000-goal[0], 4000-goal[1])+600
                if actual_distance+uncertainty > 350 or actual_distance < 100 or route["length_m"]+2400+exit_reserve > remaining:
                    continue
                # Arrival geometry alone does not protect a transit that crosses
                # the moving belief. Propagate its measured velocity covariance
                # without assuming that future observations will reduce it.
                elapsed, previous_target, previous_uncertainty = 0.0, [tx, ty, 0], uncertainty
                for left, right in zip(route["points"], route["points"][1:]):
                    angle_change = abs(math.remainder(right[2]-left[2], 2*math.pi))
                    arc_factor = angle_change/(2*math.sin(angle_change/2)) if angle_change > 1e-9 else 1
                    elapsed += math.dist(left[:2], right[:2])*arc_factor/4
                    target = [tx+vx*elapsed, ty+vy*elapsed, 0]
                    projected_uncertainty = uncertainty
                    if covariance is not None:
                        a = covariance[0][0]+elapsed*(covariance[0][2]+covariance[2][0])+elapsed**2*covariance[2][2]
                        b = covariance[0][1]+elapsed*(covariance[0][3]+covariance[2][1])+elapsed**2*covariance[2][3]
                        d = covariance[1][1]+elapsed*(covariance[1][3]+covariance[3][1])+elapsed**2*covariance[3][3]
                        projected_uncertainty = 2*math.sqrt(max(0, (a+d+math.hypot(a-d, 2*b))/2))
                    clearance = 60+min(60, max(previous_uncertainty, projected_uncertainty))
                    if not _separated([left, right], [previous_target, target], clearance):
                        break
                    previous_target, previous_uncertainty = target, projected_uncertainty
                else:
                    candidate = {"index": index, "pose": goal, "eta_s": eta, "angle_rad": angle,
                        "radius_m": distance, "route": route["points"], "length_m": route["length_m"],
                        "endurance_s": (remaining-route["length_m"]-exit_reserve)/4,
                        "bearing": math.atan2(goal[1]-predicted[1], goal[0]-predicted[0]),
                        "generation": boat.get("generation", 0)}
                    if best is None or eta < best["eta_s"]:
                        best = candidate
            if best:
                options.append(best)
        if options:
            feasible[boat["id"]] = options
    sizes = [len(uuvs)] if len(uuvs) <= 3 else [2, 3]
    best_score, chosen = math.inf, None
    for size in sizes:
        if size < 2:
            continue
        for members in combinations(sorted(feasible), size):
            if require_active_acquisition and len(active_members.intersection(members)) < 2:
                continue
            # Keep at least one scan-capable resource when selecting from a fleet.
            if len(uuvs) > 3 and not any(u["id"] not in members and u.get("remaining_range_m", math.inf) >= 3000 and any(c in u.get("capabilities", ["active"]) for c in ("active", "search", "scan")) for u in uuvs):
                continue
            for slots in product(*(feasible[member] for member in members)):
                if len({slot["index"] for slot in slots}) != size:
                    continue
                result["diagnostics"]["candidate_teams"] += 1
                cosine = sum(math.cos(2*slot["bearing"]) for slot in slots)
                sine = sum(math.sin(2*slot["bearing"]) for slot in slots)
                quality = 1-math.hypot(cosine, sine)/size
                if quality < 0.2:
                    continue
                etas = [slot["eta_s"] for slot in slots]
                scan_loss = sum(next(u for u in uuvs if u["id"] == member).get("search_workload", 1) for member in members)
                endurance = min(slot["endurance_s"] for slot in slots)
                score = max(etas)+0.25*(max(etas)-min(etas))+180*(1-quality)+120*(size-2)+20*scan_loss+30000/max(600, endurance)
                if score < best_score:
                    best_score, chosen = score, (members, slots, quality)
    if chosen:
        members, slots, quality = chosen
        result.update(status="succeeded", members=list(members),
            acquisition_requirements={"min_active_observers": 2 if require_active_acquisition else 0, "eligible_members": [boat["id"] for boat in uuvs
                if boat["id"] in members and "active" in boat.get("capabilities", ["active", "passive"])]},
            routes={member: slot["route"] for member, slot in zip(members, slots)},
            slots={member: {key: value for key, value in slot.items() if key not in ("route", "bearing")} for member, slot in zip(members, slots)})
        result["diagnostics"].pop("reason", None)
        result["diagnostics"].update(geometry_quality=quality, score=best_score, energy_horizon_s=600,
            solution_quality="feasible", arrival_is_tracking_success=False)
    return result


def tracking_control(pose, estimate, radius=60.0, speed=4.0, slot=0, team=None) -> float:
    """Bounded orbit control; team is an ordered list of 2/3 current own poses.

    Cooperative slots use a shared orbit and phase feedback. Two bearings seek
    a quarter-turn separation, not opposing collinear observation positions.
    """
    x, y, heading = valid_pose(pose)
    radius, speed = positive(radius, "radius"), positive(speed, "speed")
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 2:
        raise ValueError("tracking slot must be zero, one or two")
    if not isinstance(estimate, dict) or not {"x", "y", "vx", "vy"} <= estimate.keys():
        raise ValueError("tracking estimate requires x, y, vx, vy")
    tx, ty, vx, vy = [finite(estimate[key], key) for key in ("x", "y", "vx", "vy")]
    dx, dy = x - tx, y - ty
    distance = math.hypot(dx, dy)
    angle = math.atan2(dy, dx) if distance > 1e-6 else heading - math.pi / 2
    desired_distance = max(180.0 + 45.0 * slot, 2.5 * radius)
    if team is not None:
        if not isinstance(team, (list, tuple)) or len(team) not in (2, 3) or slot >= len(team):
            raise ValueError("team requires two or three ordered poses and a matching slot")
        team_poses = [valid_pose(member) for member in team]
        offsets = [0, math.pi/2] if len(team) == 2 else [i*2*math.pi/3 for i in range(3)]
        phases = [math.atan2(member[1]-ty, member[0]-tx)-offset for member, offset in zip(team_poses, offsets)]
        center_phase = math.atan2(sum(math.sin(phase) for phase in phases), sum(math.cos(phase) for phase in phases))
        phase_error = math.remainder(center_phase+offsets[slot]-angle, 2*math.pi)
        # A lagging observer takes the inner orbit to gain angular speed while
        # each vehicle retains its fixed forward speed and bounded curvature.
        desired_distance = max(2.5*radius, 220-max(-45, min(45, 75*phase_error)))
    radial = max(-0.8 * speed, min(0.8 * speed, (desired_distance - distance) * 0.035))
    orbit_weight = max(0.0, min(1.0, 1-(distance-desired_distance-30)/180))
    desired_x = vx + radial * math.cos(angle) - speed * orbit_weight * math.sin(angle)
    desired_y = vy + radial * math.sin(angle) + speed * orbit_weight * math.cos(angle)
    desired_heading = math.atan2(desired_y, desired_x)
    error = math.remainder(desired_heading - heading, 2 * math.pi)
    curvature = orbit_weight / desired_distance + 1.5 * error / radius
    return max(-1 / radius, min(1 / radius, curvature))


def follow_path(pose, points, radius=60.0, lookahead=40.0) -> float:
    x, y, heading = valid_pose(pose)
    radius, lookahead = positive(radius, "radius"), positive(lookahead, "lookahead")
    if not points:
        raise ValueError("cannot follow an empty path")
    points = [valid_pose(point) for point in points]
    # Heading breaks ties where a closed route crosses itself.
    nearest = min(range(len(points)), key=lambda index: math.hypot(points[index][0] - x, points[index][1] - y) + radius * 0.2 * abs(math.remainder(points[index][2] - heading, 2 * math.pi)))
    target = points[nearest]
    travelled = 0.0
    closed = len(points) > 2 and math.dist(points[0][:2], points[-1][:2]) < 1e-6
    for offset in range(1, len(points)):
        index = nearest + offset
        if index >= len(points):
            if not closed:
                break
            index %= len(points)
        candidate = points[index]
        travelled += math.dist(target[:2], candidate[:2])
        target = candidate
        if travelled >= lookahead:
            break
    dx, dy = target[0] - x, target[1] - y
    square = dx * dx + dy * dy
    if math.cos(heading) * dx + math.sin(heading) * dy < 0:
        error = math.remainder(math.atan2(dy, dx) - heading, 2 * math.pi)
        return math.copysign(1 / radius, error)
    curvature = 0.0 if square < 1e-12 else 2 * (-math.sin(heading) * dx + math.cos(heading) * dy) / square
    return max(-1 / radius, min(1 / radius, curvature))
