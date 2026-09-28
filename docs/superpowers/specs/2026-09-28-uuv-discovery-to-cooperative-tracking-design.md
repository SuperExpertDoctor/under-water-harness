# Discovery-to-Cooperative-Tracking Design

## Research question and scope

Study how a fleet of eight searching UUVs changes tasks after **one** UUV first
detects the **single** moving target. The observable outcome is not a command to
follow the target, but the first interval of valid cooperative passive tracking
by two or three UUVs. This design refines the tracking transition in the
[mission-control v2 design](2026-09-27-uuv-mission-control-v2-design.md) and the
[waypoint and sonar design](2026-09-28-uuv-waypoint-sonar-design.md). It does not
change the PI SDK, the enemy's partial-observation boundary, or the game's
assumption of immediate reliable communication among own UUVs.

## Choice of approach

- Always assign the discoverer as leader: simple, but a boat with too little
  energy or poor geometry can prevent a viable team from forming.
- Reassign the whole team immediately: flexible, but abandoning the only
  current observer can lose the target during the handoff.
- **Chosen:** the discoverer temporarily maintains contact under an explicitly
  preauthorized, bounded local policy. The scheduler prioritizes it for the
  permanent team only when its sensor, route and energy remain feasible. A
  support boat must obtain its own observation before the discoverer may be
  relieved. No guarantee of continuous observation is implied.

## Information and authority

The discovering boat shares an observation ID, contact ID, measurement time,
sensor mode, its own pose/generation, measured active range and bearing where
available, and the estimated target state `[x, y, vx, vy]` with covariance and
last-seen time. When the first contact is side-scan, it is still only a noisy
measurement; subsequent forward-active observations must meet their own range,
field-of-view and occlusion rules. The shared estimate, never simulator truth,
is the sole target input to selection and navigation. The other UUVs may know
their teammates' own states, but not an unseen target's actual pose. Duplicate
reads do not produce new measurements.

Before a mission starts, the operator can grant a **provisional contact-hold**
policy with configured maximum duration, maneuver domain and energy reserve.
In all three permission modes, confirmation may then atomically suspend the
discoverer's search assignment, mark its unfinished coverage as an explicit
gap, and start bounded local active acquisition without waiting for another
LLM turn. This is a preapproved response to a sensor event, **not** approval to
recruit supporting boats or repartition regions. New member assignments and
region repair still pass the existing permission gate; in Request mode, they
wait for a person. If the policy was not granted, no autonomous task switch is
allowed: keep only the previously authorized safe motion and request approval;
do not claim immediate contact hold. Approval against a stale contact, member
generation, route or region version must be rejected and recomputed. Neither
waiting for approval nor an unavailable LLM stops the simulation clock.

## State transitions and responsibility

1. **Search / first confirmation:** one boat generates a confirmed noisy
   contact. The event is broadcast once; PI is notified without steering any
   individual tick. The discoverer becomes `provisional_contact_hold` only if
   the bounded policy applies. It remains one physical UUV with one main task
   and occupies one of the maximum three contact-team places.
2. **Selection / pending approval:** enumerate feasible two-boat teams first;
   compare three-boat teams when needed. Filter by passive and forward-active
   sensor capability, Dubins reachability, energy/exit reserve, observation
   opportunity, arrival time, expected bearing geometry and scan coverage
   loss. Prioritize the discoverer if feasible, not unconditionally. The
   proposal includes replacement search ownership and an explicit approval
   receipt; proposal generation itself changes no assignments.
3. **Support transit:** after authorization, each selected boat follows its
   own bounded-curvature, conflict-checked route to an **observation entry
   pose**, not the target's coordinate. Its destination predicts target motion
   over travel time and specifies both position and heading. The plan checks
   probable sensor range, line of sight, active front-sonar cone, safe clearance
   and ability to keep moving through an observation window. At least two
   team members must be active-capable for initial contact establishment.
4. **Contact acquisition:** arriving at a route endpoint alone does not count.
   A dedicated short-horizon acquisition controller uses the latest shared
   estimate to choose safe observation waypoints whose resulting headings put
   the predicted target inside the forward-active cone. It replans from
   observations when the target moves, bounds turn curvature and maneuvers,
   and cannot assume either sensor will detect the target. Two team members
   must actually return accepted, fresh active measurements in the same sensor
   cycle, with sufficient bearing geometry and bounded estimate uncertainty.
5. **Passive cooperative tracking:** only after active acquisition does the
   team switch to passive mode. At least two actual team passive bearing
   measurements, adequate geometry and bounded uncertainty must persist for
   the configured tracking streak before counting effective tracking time.
   Passive bearings never masquerade as measured target positions. If the
   discoverer is being relieved from a two-boat team, release it only after
   the incoming third observer has established real observations; a full
   team follows the separate two-survivor rule below and remains subject
   to its energy-safe exit deadline.

At every normal step, the provisional discoverer and all supporting boats
together never exceed three contact-team members. In a two-boat team, an
incoming third observer can establish contact before the discoverer is
released. In a three-boat team, the remaining two must first be capable of
maintaining observation before a place is released for a replacement; if they
cannot, report degraded or lost contact rather than briefly using a fourth
tracker. Each UUV has exactly one main task, and the number of active search
regions equals the number of boats currently eligible and assigned to scan.
The provisional boat and tracking
transits are not counted as scanning; their historical effective side-scan
coverage is retained, not cleared. A temporarily uncovered region is visibly
recorded as a gap until an authorized local repair makes it executable. No
active or passive tracking measurement updates side-scan coverage timestamps.

## Failure and fallback

- If the discoverer loses sight while help is in transit, propagate the
  timestamped estimate and covariance, show contact degradation/loss, and
  switch to authorized active reacquisition. Do not continue counting passive
  tracking seconds or navigate using hidden target truth.
- If no safe route or two-observer acquisition window is feasible, explain
  which condition failed. Use another eligible team or a new observed estimate;
  do not relax sonar cone, motion limits or collision constraints. A failure
  with no authorized safe continuation causes a protective pause.
- When the provisional duration or energy reserve expires, seek authorized
  handoff or return to a feasible approved search/exit plan. A forward-only UUV
  must never be told to stop in place. No approval may be silently inferred
  from a chat reply or an LLM-selected candidate.

## Boundaries and evaluation

`tools/uuv_game/sensing.py` owns one-cycle observations and contact state;
`algorithms/tracking.py` and the existing single-UUV path planner provide
team/entry-pose candidates; `runtime.py` controls the bounded transition and
per-tick safety checks. The Python permission gate owns preauthorization,
submission and delayed-approval revalidation; PI selects among validated
candidates and explains the coverage tradeoff. All maneuver thresholds and
time budgets live in `configs/`, not hidden in prompts or controller literals.

Report first-detection-to-first-support-observation time,
first-detection-to-valid-passive-tracking time, acquisition success rate,
unobserved/lost intervals, scan coverage and revisit cost, rejected unsafe
routes and provisional-policy expiry. The UI must distinguish detected,
provisional hold, approved transit, arrived, actively acquired and effective
passive tracking; an assigned team is not displayed as tracking success.

Deterministic tests must cover: discovery while approval is pending; no
preauthorization; target outside an arrived boat's forward-active cone; only
one of two boats sensing; both boats sensing in the same cycle; true passive
bearings and the configured streak; moving/lost target and stale approvals;
coverage gap/repair, energy expiry and no safe path. Re-run both existing
long-scenario failures and a held-out seed with unchanged acceptance criteria.
The separate PI/LongCat integration test checks that the simulation advances
while the model or a human approval is pending; offline simulation success is
not evidence that a live provider was exercised.
