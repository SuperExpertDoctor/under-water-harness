"""Bounded geometric pursuit plus receding-horizon cooperative coverage control.

`tracking_control` implements a simplified form of receding-horizon cooperative
tracking (GA-free): each member scores a short list of constant-curvature
rollouts by how many future steps keep the target inside someone's passive
forward sector, evaluated against the target's nominal velocity prediction and
both max-turn reachable-set boundary hypotheses. Marginal coverage over
dead-reckoned teammates makes separated observers prefer complementary sectors.
"""

import math
from itertools import combinations, product

from .control import _separated
from .motion import finite, integrate, positive, valid_pose
from .planning import plan_path
from .conflicts import resolve_conflicts
from ..config import Config, algorithm_settings

_TRACK = algorithm_settings("tracking")


def tracking_plan(uuvs, contact, obstacles, *, require_active_acquisition=True, required_members=None):
    """Enumerate bounded two/three-boat geometry using actual Dubins transits.

    Each slot is an arrival reference around a predicted moving contact, never
    a stationary control target. Larger input fleets trigger team selection.
    Members listed in required_members (e.g. the discovering boat whose
    measurements feed the predicted slot centers) must appear in every team.
    """
    if not isinstance(uuvs, list) or not 1 <= len(uuvs) <= Config().fleet_size:
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
    required = {member for member in (required_members or []) if member in identifiers}
    result.update(acquisition_mode="active_until_cooperative_geometry" if require_active_acquisition else "passive_existing_team",
        acquisition_requirements={"min_active_observers": 2 if require_active_acquisition else 0, "eligible_members": sorted(active_members)},
        required_members=sorted(required))
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
        if remaining < _TRACK["minimum_remaining_range_m"] or uncertainty >= _TRACK["maximum_uncertainty_m"]:
            continue
        options = []
        for index in range(_TRACK["slot_count"]):
            angle = index*math.tau/_TRACK["slot_count"]
            best = None
            for distance in _TRACK["slot_radii_m"]:
                if distance+uncertainty > _TRACK["sensor_range_m"]:
                    continue
                eta = math.dist(start[:2], [tx, ty])/Config().speed
                route = None
                for _ in range(_TRACK["candidate_iterations"]):
                    center = [tx+vx*eta, ty+vy*eta]
                    goal = [center[0]+distance*math.cos(angle), center[1]+distance*math.sin(angle), 0]
                    goal[2] = math.atan2(center[1]-goal[1], center[0]-goal[0])
                    route = plan_path(start, goal, obstacles=obstacles, budget=_TRACK["planning_entry_budget"])
                    result["diagnostics"]["candidate_routes"] += 1
                    if route["status"] != "succeeded":
                        break
                    eta = route["length_m"]/Config().speed
                if route["status"] != "succeeded":
                    continue
                predicted = [tx+vx*eta, ty+vy*eta]
                actual_distance = math.dist(goal[:2], predicted)
                exit_reserve = min(goal[0], goal[1], Config().width-goal[0], Config().height-goal[1])+_TRACK["exit_reserve_m"]
                if (actual_distance+uncertainty > _TRACK["sensor_range_m"] or
                    actual_distance < _TRACK["minimum_slot_distance_m"] or
                    route["length_m"]+_TRACK["tracking_range_reserve_m"]+exit_reserve > remaining):
                    continue
                # Arrival geometry alone does not protect a transit that crosses
                # the moving belief. Propagate its measured velocity covariance
                # without assuming that future observations will reduce it.
                elapsed, previous_target, previous_uncertainty = 0.0, [tx, ty, 0], uncertainty
                for left, right in zip(route["points"], route["points"][1:]):
                    angle_change = abs(math.remainder(right[2]-left[2], 2*math.pi))
                    arc_factor = angle_change/(2*math.sin(angle_change/2)) if angle_change > 1e-9 else 1
                    elapsed += math.dist(left[:2], right[:2])*arc_factor/Config().speed
                    target = [tx+vx*elapsed, ty+vy*elapsed, 0]
                    projected_uncertainty = uncertainty
                    if covariance is not None:
                        a = covariance[0][0]+elapsed*(covariance[0][2]+covariance[2][0])+elapsed**2*covariance[2][2]
                        b = covariance[0][1]+elapsed*(covariance[0][3]+covariance[2][1])+elapsed**2*covariance[2][3]
                        d = covariance[1][1]+elapsed*(covariance[1][3]+covariance[3][1])+elapsed**2*covariance[3][3]
                        projected_uncertainty = 2*math.sqrt(max(0, (a+d+math.hypot(a-d, 2*b))/2))
                    clearance = _TRACK["transit_target_clearance_m"]+min(_TRACK["transit_uncertainty_margin_m"], max(previous_uncertainty, projected_uncertainty))
                    if not _separated([left, right], [previous_target, target], clearance):
                        break
                    previous_target, previous_uncertainty = target, projected_uncertainty
                else:
                    candidate = {"index": index, "pose": goal, "eta_s": eta, "angle_rad": angle,
                        "radius_m": distance, "route": route["points"], "length_m": route["length_m"],
                        "endurance_s": (remaining-route["length_m"]-exit_reserve)/Config().speed,
                        "bearing": math.atan2(goal[1]-predicted[1], goal[0]-predicted[0]),
                        "generation": boat.get("generation", 0)}
                    if best is None or eta < best["eta_s"]:
                        best = candidate
            if best:
                options.append(best)
        if options:
            feasible[boat["id"]] = options
    sizes = [len(uuvs)] if len(uuvs) <= 3 else [2, 3]
    ranked = []
    for size in sizes:
        if size < 2:
            continue
        for members in combinations(sorted(feasible), size):
            if required and not required.issubset(members):
                continue
            if require_active_acquisition and len(active_members.intersection(members)) < 2:
                continue
            # Keep at least one scan-capable resource when selecting from a fleet.
            if len(uuvs) > 3 and not any(u["id"] not in members and u.get("remaining_range_m", math.inf) >= _TRACK["minimum_remaining_range_m"] and any(c in u.get("capabilities", ["active"]) for c in ("active", "search", "scan")) for u in uuvs):
                continue
            for slots in product(*(feasible[member] for member in members)):
                if len({slot["index"] for slot in slots}) != size:
                    continue
                result["diagnostics"]["candidate_teams"] += 1
                cosine = sum(math.cos(2*slot["bearing"]) for slot in slots)
                sine = sum(math.sin(2*slot["bearing"]) for slot in slots)
                quality = 1-math.hypot(cosine, sine)/size
                if quality < _TRACK["geometry_quality_min"]:
                    continue
                etas = [slot["eta_s"] for slot in slots]
                scan_loss = sum(next(u for u in uuvs if u["id"] == member).get("search_workload", 1) for member in members)
                endurance = min(slot["endurance_s"] for slot in slots)
                score = (max(etas)+_TRACK["arrival_spread_weight"]*(max(etas)-min(etas))+
                    _TRACK["geometry_weight"]*(1-quality)+_TRACK["third_observer_cost"]*(size-2)+
                    _TRACK["search_loss_weight"]*scan_loss+
                    _TRACK["energy_cost_weight"]/max(_TRACK["track_energy_horizon_s"], endurance))
                ranked.append((score, members, slots, quality))
    chosen = None
    for score, members, slots, quality in sorted(ranked, key=lambda item: (item[0], item[1]))[:_TRACK["candidate_team_limit"]]:
        starts = {boat["id"]: boat["pose"] for boat in uuvs if boat["id"] in members}
        routes = {member: slot["route"] for member, slot in zip(members, slots)}
        joint = resolve_conflicts(starts, {member: slot["pose"] for member, slot in zip(members, slots)}, routes, obstacles)
        if joint["status"] == "succeeded":
            chosen = (score, members, slots, quality, joint)
            break
    if chosen:
        best_score, members, slots, quality, joint = chosen
        result.update(status="succeeded", members=list(members),
            acquisition_requirements={"min_active_observers": 2 if require_active_acquisition else 0, "eligible_members": [boat["id"] for boat in uuvs
                if boat["id"] in members and "active" in boat.get("capabilities", ["active", "passive"])]},
            routes=joint["routes"],
            slots={member: {key: value for key, value in slot.items() if key not in ("route", "bearing")} for member, slot in zip(members, slots)})
        result["diagnostics"].pop("reason", None)
        result["diagnostics"].update(geometry_quality=quality, score=best_score,
            energy_horizon_s=_TRACK["track_energy_horizon_s"],
            conflict_solver=joint["diagnostics"]["algorithm"], conflict_nodes=joint["diagnostics"]["expanded"],
            solution_quality="feasible", arrival_is_tracking_success=False)
    elif required and not required.issubset(feasible):
        result["diagnostics"]["reason"] = "required_member_has_no_feasible_slot"
    elif required:
        result["diagnostics"]["reason"] = "no_team_contains_all_required_members"
    return result


def acquisition_control(pose, estimate, radius=Config().radius, speed=Config().speed, *, reacquiring=False):
    """Turn toward the shared estimate while preserving a safe moving standoff."""
    x, y, heading = valid_pose(pose)
    radius, speed = positive(radius, "radius"), positive(speed, "speed")
    tx, ty, vx, vy = [finite(estimate[key], key) for key in ("x", "y", "vx", "vy")]
    tx += vx*_TRACK["acquisition_prediction_s"]
    ty += vy*_TRACK["acquisition_prediction_s"]
    standoff = _TRACK["reacquisition_standoff_m"] if reacquiring else _TRACK["acquisition_standoff_m"]
    if math.dist([x, y], [tx, ty]) <= standoff:
        # Acquisition/reacquisition runs the narrow forward-active beam, so the
        # station-keeping rollout must score detection in that sector, not the
        # wide passive sector used by established tracking.
        return tracking_control(pose, estimate, radius=radius, speed=speed,
                                sector_rad=math.radians(Config().forward_active_half_angle_deg))
    error = math.remainder(math.atan2(ty-y, tx-x)-heading, math.tau)
    return max(-1/radius, min(1/radius, _TRACK["acquisition_heading_gain"]*error/radius))


def _detects(pose, point, sensor_range, half_angle_rad):
    """Passive forward sector: range plus a wide azimuth window."""
    if math.dist(pose[:2], point[:2]) > sensor_range:
        return False
    bearing = math.atan2(point[1]-pose[1], point[0]-pose[0])
    return abs(math.remainder(bearing-pose[2], 2*math.pi)) <= half_angle_rad


def tracking_control(pose, estimate, radius=Config().radius, speed=Config().speed, slot=0, team=None, sector_rad=None) -> float:
    """Receding-horizon cooperative coverage control (simplified GA analogue).

    Instead of a fixed orbit law, each member evaluates a small set of constant-
    curvature rollouts over a prediction horizon. The score counts, at every
    horizon step and every target hypothesis (nominal velocity plus both
    reachable-set boundary turns), whether the hypothesis lies inside someone's
    passive forward sector. Marginal coverage over dead-reckoned teammates makes
    observers cover complementary exits instead of bunching on one bearing;
    a standoff floor and a distance pull keep a lone observer on station.
    """
    x, y, heading = valid_pose(pose)
    radius, speed = positive(radius, "radius"), positive(speed, "speed")
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= 2:
        raise ValueError("tracking slot must be zero, one or two")
    if not isinstance(estimate, dict) or not {"x", "y", "vx", "vy"} <= estimate.keys():
        raise ValueError("tracking estimate requires x, y, vx, vy")
    tx, ty, vx, vy = [finite(estimate[key], key) for key in ("x", "y", "vx", "vy")]
    if team is not None:
        if not isinstance(team, (list, tuple)) or len(team) not in (2, 3) or slot >= len(team):
            raise ValueError("team requires two or three ordered poses and a matching slot")
        teammates = [(index, valid_pose(member)) for index, member in enumerate(team) if index != slot]
    else:
        teammates = []
    sensor_range = _TRACK["sensor_range_m"]
    half_angle = sector_rad if sector_rad is not None else math.radians(Config().forward_passive_half_angle_deg)
    target_speed = math.hypot(vx, vy)
    horizons = _TRACK["rh_horizons_s"]
    # Fixed-speed members cannot hover, so the formation keeps orbiting: anchor
    # the *relative* phase (bearing minus slot offset) to the team's circular
    # mean rather than an absolute bearing. Sector diversity then persists
    # while the whole formation slowly rotates around the target.
    offsets = {2: (0.0, math.pi/2), 3: tuple(i*2*math.pi/3 for i in range(3))}
    mate_offsets = [offsets[len(team)][index] for index, _ in teammates] if team is not None else []
    own_offset = offsets[len(team)][slot] if team is not None else 0.0
    fractions = _TRACK["rh_curvature_fractions"]
    spacing = min((b-a for a, b in zip(sorted(fractions), sorted(fractions)[1:])), default=0.0)

    def coverage_score(fraction):
        curvature = fraction/radius
        rollout = [integrate([x, y, heading], speed, curvature, horizon) for horizon in horizons]
        score = -_TRACK["rh_smoothness_weight"]*abs(fraction)
        for horizon, own in zip(horizons, rollout):
            mates = [integrate(member, speed, 0.0, horizon) for _, member in teammates]
            turn = min(math.pi, target_speed*horizon/_TRACK["rh_evade_turn_radius_m"])
            base = math.atan2(vy, vx) if target_speed > 1e-6 else 0.0
            coverage = 0.0
            for drift in (-turn, 0.0, turn):
                hypothesis = [tx+target_speed*math.cos(base+drift)*horizon, ty+target_speed*math.sin(base+drift)*horizon]
                own_hit = _detects(own, hypothesis, sensor_range, half_angle)
                covered = own_hit or any(_detects(mate, hypothesis, sensor_range, half_angle) for mate in mates)
                coverage += _TRACK["rh_coverage_weight"]*covered+_TRACK["rh_redundancy_weight"]*own_hit
            nominal = [tx+vx*horizon, ty+vy*horizon]
            bearings = [math.atan2(member[1]-nominal[1], member[0]-nominal[0]) for member in [own, *mates]]
            cosine = sum(math.cos(2*bearing) for bearing in bearings)
            sine = sum(math.sin(2*bearing) for bearing in bearings)
            diversity = 1-math.hypot(cosine, sine)/len(bearings) if mates else 0.0
            distance = math.dist(own[:2], nominal)
            anchor = 0.0
            if mates:
                phases = [bearings[0]-own_offset]+[bearing-offset for bearing, offset in zip(bearings[1:], mate_offsets)]
                center = math.atan2(sum(math.sin(phase) for phase in phases), sum(math.cos(phase) for phase in phases))
                anchor = math.cos(phases[0]-center)
            # Chirality consensus: coverage and phase scores are symmetric in
            # orbit direction, so without this bias the pair can counter-rotate
            # and periodically cross the collinear (|sin|=0) geometry. Measure
            # the rollout's bearing drift, not heading tangency: approaching a
            # far target barely changes bearing and stays neutral.
            chirality = math.sin(math.remainder(bearings[0]-math.atan2(y-ty, x-tx), 2*math.pi))
            score += (coverage+_TRACK["rh_diversity_weight"]*diversity+_TRACK["rh_slot_weight"]*anchor
                      +_TRACK["rh_orbit_sign_weight"]*chirality
                      -_TRACK["rh_distance_weight"]*abs(distance-_TRACK["rh_preferred_m"])/sensor_range
                      -(_TRACK["rh_hard_penalty"] if distance < _TRACK["rh_hard_floor_m"] else 0.0))
        return score

    # Discrete curvature families cannot hold station, so after picking the
    # best coarse family refine it twice at quarter spacing; the ~0.03
    # fraction resolution reaches the shallow curvature a station orbit needs.
    best_fraction = max(fractions, key=coverage_score)
    for _ in range(2 if spacing else 0):
        spacing /= 4
        candidates = [max(-1.0, min(1.0, best_fraction+delta*spacing)) for delta in (-1, 1)]
        best_fraction = max([best_fraction, *candidates], key=coverage_score)
    return max(-1/radius, min(1/radius, best_fraction/radius))


def follow_path(pose, points, radius=Config().radius, lookahead=_TRACK["lookahead_m"]) -> float:
    x, y, heading = valid_pose(pose)
    radius, lookahead = positive(radius, "radius"), positive(lookahead, "lookahead")
    if not points:
        raise ValueError("cannot follow an empty path")
    points = [valid_pose(point) for point in points]
    # Heading breaks ties where a closed route crosses itself.
    nearest = min(range(len(points)), key=lambda index: math.hypot(points[index][0] - x, points[index][1] - y) + radius * _TRACK["follow_heading_penalty_fraction"] * abs(math.remainder(points[index][2] - heading, 2 * math.pi)))
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
