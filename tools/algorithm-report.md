# Algorithm Core Report

## Scope and Interfaces

Implemented Task 1 only in `uuv_game/algorithms`, `uuv_game/vendor`, and
`tests/test_algorithms.py`. Public exports preserve the exact signatures in
`IMPLEMENTATION.md`. Inputs and outputs are plain serializable dictionaries and
lists. Coordinates are meters, angles radians, time seconds. The algorithms
only compute candidates or controls; they never execute or approve missions.

## Implemented Methods

- Motion: exact forward constant-curvature integration using a stable midpoint
  sinc formulation; positive speed, nonnegative timestep and finite validation.
- Paths: all six analytic Dubins families, sorted by physical path length.
  Safe direct candidates are attempted first. A bounded weighted forward
  pose-lattice search then expands five constant-curvature controls and connects
  to the goal using Dubins paths. No reverse motion or instantaneous turns.
- Geometry: swept segment/circle collision checks with an 8 m default vehicle
  envelope and conservative circular-arc sagitta padding. Map edges include the
  same envelope. Sampling includes primitive junctions and uses at most 5 m
  distance and 1/12 rad heading increments.
- Allocation: SciPy linear assignment to explicit task slots. Each vehicle is
  used at most once; each requested team contains one to three members. Costs
  are straight-line distance weighted by task priority. Insufficient vehicles
  produce `infeasible`, not partial silently reduced teams.
- Search: heuristic alternating strip sweeps with checked Dubins connectors,
  including closure back to each vehicle's original pose.
- Tracking: moving-center orbit vector field with bounded radial correction;
  slots use 180/225/270 m nominal orbit radii, expanded when turning radius
  requires it. This handles stationary targets without stationary trailing.
- Route following: bounded pure pursuit with heading-aware nearest point,
  closed-route wrapping, and an explicit turn when the reference lies behind.

## Provenance

The Dubins analytic kernel is adapted from MIT-licensed PythonRobotics commit
`b2020cd613d0709e9c0f38a7579f1a681cf7a227`, retrieved 2026-09-27. The adjacent
`vendor/LICENSE` preserves copyright and permissions. `vendor/PROVENANCE.md`
links the exact source and identifies all adaptations. No upstream plotting,
CLI, large repository assets, or implicit import-path modifications are used.

## Verification

Command:

```sh
PYTHONPATH=tools python -m pytest tools/tests/test_algorithms.py -q
```

- Initial RED: missing module assertion, `1 failed, 19 skipped` (before any
  production algorithm implementation). An earlier fixture-only draft produced
  missing-module setup errors and was corrected to a proper failing assertion.
- Initial GREEN: `20 passed in 1.08s`.
- Added regressions reproduced facing-away zero steering and malformed input
  KeyErrors: `3 failed, 25 passed`; fixes produced `28 passed in 1.10s`.
- Added malformed search/path input and resource-bound regressions. The extreme
  radius/tiny-step case was interrupted after it demonstrated excessive sample
  generation; preflight sample-bound validation now rejects it immediately.
- Final GREEN: `32 passed in 1.19s`.

Coverage includes exact straight/quarter-circle integration, finite validation,
all six Dubins family endpoints, detour and curvature continuity, thin obstacle
swept checks, arc sagitta collision, infeasible and exhausted-budget statuses,
unique allocation/max-three, closed search routes including all eight vehicles
on the default map, 800 simulated seconds of stationary/slow-target orbit
control, behind-reference steering, and malformed/oversized inputs.

The root coordinator runs `npm run check` after integrating all concurrent
changes. This Python-only subtask did not run builds, npm tests, paid provider
calls, or modify dependencies. No commit was made.

## Explicit Limits and Runtime Responsibilities

- Search is heuristic and not complete or globally optimal. Success reports
  `solution_quality: feasible`; failure describes exhaustion or an unsafe
  endpoint. `timed_out` means node-expansion budget exhaustion, not a measured
  wall-clock timeout. Default budget is 3000, supported range 1..20000.
- Planner limits: radius 1..100000 m, requested sampling step >=0.1 m, map side
  <=100000 m, and a conservative maximum of 50000 samples per connector. The
  last condition normally imposes a much lower practical radius/map limit.
- Geometry safety assumes dense piecewise constant-curvature input. It does
  not certify arbitrary unknown curves hidden between supplied samples, target
  avoidance, or inter-vehicle separation. The runtime must validate actual
  next-step motion, separation, and any emergency pause using its shared gate.
- Independent search strips are not a fleet deconfliction algorithm. Narrow
  strips (`width <= 2 * (2 * radius + 16)`) return infeasible. Obstacles at sweep
  endpoints may make a heuristic route fail even if another coverage plan
  exists. Coverage completeness and optimality are not claimed.
- Integration hardening treats unassigned stationary UUVs as planning obstacles
  and shifts blocked sweep endpoints to a checked location within the assigned
  strip. Every connector still uses the same bounded Dubins/forward search;
  this does not imply a completeness guarantee.
- Tracking and route following are geometric heuristics, not physical UUV
  dynamics or stability proofs. Fast targets, boundaries, obstacles and fleet
  interactions require runtime safety enforcement. Fixed positive navigation
  speed remains a runtime configuration and control invariant.
- Allocation optimizes the stated independent slot cost only; it does not
  optimize Dubins travel cost, synchronized arrival, communication, or formation
  interactions. All requested tasks must fit; selection among oversubscribed
  task sets is an upstream mission decision.
