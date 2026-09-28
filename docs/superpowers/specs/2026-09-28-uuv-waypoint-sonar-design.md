# UUV Waypoint Planning and Sonar Design

## Objective

Keep the existing PI SDK mission workflow while making 2D sensing, waypoint planning, multi-vehicle conflict resolution, continuous search and cooperative tracking describe the same game state. Eight UUVs remain in play, at most three follow the single partly observable target. Human approval and safety enforcement remain in Python; the LLM chooses mission intentions, never raw controls.

## Sensors and metrics

- Side-scan has port and starboard swaths relative to current heading, a near-field blind band and an outer range. Only searchable cell centers inside an unobstructed effective side-scan swath, during a search task, update `scan_times`.
- For this game, a target inside a side-scan swath can yield a noisy active range/bearing contact. Forward active acquisition also returns noisy range/bearing, within its forward cone, but never updates `scan_times`. Passive tracking returns bearing alone. No sonar imaging or frequency simulation is required.
- The front-active safety sensor supplies only observed obstacles to the local route planner. The simulator retains a private truth-based collision interlock. A change in contact state cannot turn a passive ping into a search ping.
- `coverage_metrics` maintains the fixed searchable denominator, cumulative unique coverage and mission-configurable recent-window unique coverage. `scan_half_life_s` controls color fading, not revisit deadlines. A stale, unscheduled cell is a revisit gap; an ongoing feasible scan of the cell is not a scheduling gap.
- Front-end sensor labels and scan footprints must reflect the actual active instrument. Approval cards appear in conversation automatically for pending approvals without removing the existing task/approval details view.

## Waypoint planning

- Use A* over a forward-only discretized pose lattice `(x,y,heading)`, with constant-curvature motion primitives, swept collision checks, a bounded search budget and Dubins analytic connection. The returned poses are physical waypoints, not direct simulator movement commands.
- A CBS high-level conflict tree detects predicted swept separation violations over time among up to eight route transits; a constrained lower-level pose A* replans only the affected UUV. No zero-speed grid waits are allowed: legal delay must come from an admissible moving route or start maneuver. A bounded exhaustion is an infeasible candidate, not permission to move into a conflict.
- The existing per-tick safety controller remains as a last resort if routes become stale or unexpected contacts appear. Conflicts must be checked before approving and activating joint mission routes, including search repair and tracking transits.

## Tracking lifecycle

- Fused noisy bearings update the existing constant-velocity EKF; no target truth enters the estimator or mission planner. Candidate observer slots favor useful intersection angles, reachability, energy and ongoing coverage. Tracking transit routes are conflict checked before submission.
- `transit`, `acquiring`, `tracking`, `degraded` and `reacquiring` remain distinct. Merely assigning a waypoint does not earn tracking time; at least two actual passive observations with sufficient geometry must establish cooperative tracking.
- Search regions cover the searchable map with one responsible eligible UUV each; current authorized routes continuously revisit them. Task changes preserve scan timestamps and repair only gaps. Energy exit and replacement preserve eight physical slots.

## Acceptance

Deterministic tests prove side-vs-forward swath and angle rotation, duplicate scan accounting, configurable rolling-window expiry, incomplete observation isolation, Dubins bounded curvature, continuous-time crossing conflict detection/resolution or declared infeasibility, tracking acquisition and safe region repair. UI tests prove pending approval is visible automatically and sensor/mode labels match backend telemetry. Run Python tests, UI tests and repository checks; do not claim a successful real-LLM integration without a live run.
