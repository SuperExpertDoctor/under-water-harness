# Algorithm and Runtime Report, V2

## Status and Scope

This report describes the current implementation as of 2026-09-27, not an
acceptance certificate. A pre-acquisition-bridge version passed four simulated
hours and 30 wall-clock minutes together with tracking and natural rotations.
The current version adds explicit active acquisition and passes the unchanged
three-observer scenario plus held-out offline validation: 14400 simulated seconds
over 1800.27 wall seconds, 24 natural replacements, eight completed handoffs from
nine attempts, and no pauses or invariant failures. The seed-20260928 report is
`outputs/v2-acceptance-active-bridge-heldout-20260928.json`; it records 11005
effective tracking seconds and 572 lost-contact seconds. Tuning used seed 42.
The driver sets its own pacing; its recorded pre-live-pacing configuration is
not evidence of provider-online operation. The live-model soak is separate.
Pre-bridge evidence does not certify the current version.

The implementation spans `uuv_game/algorithms`, `observations.py`, `sensing.py`,
`mission_planning.py`, `lifecycle.py`, `handover.py` and `runtime.py`. Python owns
mission state and execution. PI selects and submits candidates; it does not
steer individual ticks or approve itself. Coordinates are meters, headings
radians and time seconds. The default game uses eight own boats, a 4000 m
square map, 4 m/s forward motion and a 60 m minimum turn radius.

## Motion and Joint Control

- Exact constant-curvature integration uses the existing midpoint sinc
  formulation. Motion is forward only; no reverse motion or instantaneous turns.
- Planning tries all six analytic Dubins families, ordered by path length,
  then uses a bounded forward pose-lattice search when direct paths fail.
  Geometry checks include swept segments, obstacle envelopes and arc padding.
- Search route following uses bounded pure pursuit. Search paths contain an
  entry transit followed by an explicitly closed patrol; entry itself need not
  be part of the repeating cycle.
- `algorithms/control.py` jointly selects simultaneous curvatures before any
  pose is committed. It first checks preferred controls, then compares a
  bounded set per boat with a width-32 beam. Predictions extend to 18 seconds;
  pairwise checks cover the intervals between prediction samples. Stationary
  own boats, obstacles, authorized domains and observed contact estimates are
  constraints. Failure produces a protective pause.

This controller is a finite-horizon heuristic, not a complete collision-free
fleet scheduler or a stability proof. Conservative rejection and later dead
ends are possible. True target positions are confined to the simulator's
independent protective check, not supplied to control selection. Planner
`timed_out` means search-budget exhaustion, not a measured wall-clock timeout.

## Observations and Estimation

`sensing.py` generates each sensor cycle once. Tool reads reuse retained
records with sequence cursors; repeated reads do not resample or rerun the
filter. Mode, range, obstacle visibility and passive-signal availability gate
measurements. Records include observer pose, slot, generation, sample ID and
simulation time.

Active sensing measures noisy range and bearing. Passive sensing measures only
noisy bearing, without target position, range or hidden scenario identity.
`observations.py` maintains a centralized constant-velocity extended Kalman
filter (EKF) with state `[x, y, vx, vy]`. Prediction uses elapsed simulation
time; bearing residuals are angle-normalized, innovation gating rejects large
residuals, and covariance uses the Joseph update. Missing observations leave
prediction uncertainty rather than truth-derived positions. A lost estimate
may be reinitialized from a valid active measurement when its old innovation
gate would reject reacquisition.

Current defaults are 350 m sensor range, 2 degree bearing standard deviation
and 4 m active range standard deviation. These are game parameters, not measured
acoustic performance. AIS is a separate noisy active-like observation source
for optional scene-editor surface vessels. Complex crossing-target association
is not implemented.

## Search Responsibilities and Coverage

`partition_regions` uses a four-neighbor free-water grid. It prefers local
ownership repair: departing owners merge into adjacent regions, and new owners
split existing regions while retaining connectivity. When local repair fails,
weighted connected bisection and SciPy linear assignment provide a bounded
global fallback. Work weights include unscanned and revisit demand; entry
matching checks a Dubins route and an energy estimate. Forbidden pairings use
infinite cost, not a large finite penalty.

A successful fleet assignment has one connected, nonoverlapping region per
search boat: `K = N_search`. Tracking transit, acquisition, tracking and exit
boats are excluded. Zero search boats leave unowned backlog. This is an
engineering heuristic, not a minimum-perimeter or globally optimal partition.

Region search clips alternating lanes to owned cells and uses checked Dubins
connections. Turns can use the authorized map beyond a responsibility boundary;
this does not transfer ownership. The current 500 m lane spacing is a fixed
default for the 350 m sensor range, not automatically derived for arbitrary
sensor configurations. Partial patrols expose deferred work and require energy
rotation authorization. Only actual active sensing updates the persistent
global coverage ledger; finishing a route is not proof of complete coverage.

Automatic fleet search currently plans over the whole map. An explicit bbox
request must also provide members. Explicit-region candidates preserve other
owners, exclude their cells and reject empty or disconnected remaining regions.
The explicit task-slot allocation API remains available, using priority-weighted
straight-line costs; it is not the cooperative tracking-team optimizer and
does not by itself create an executable mission.

Global coverage percentages use the same whole-cell obstacle exclusion as
partition feasibility: cells intersecting an obstacle plus its 8 m clearance
are excluded. The default map has 1584 searchable cells, not a fixed 1600-cell
denominator. Reporting this mask consistently is not evidence that every
remaining cell has actually been scanned or is reachable by a patrol route.

Frame metrics report unscanned backlog as searchable minus ever-scanned cells.
Recent coverage uses cells observed within 30 simulated minutes divided by all
searchable cells. Revisit timeliness uses the same fresh-cell count divided by
ever-scanned cells, with a null rate before any coverage. The 30-minute target
is a reporting window, not a new planning constraint.

## Cooperative Tracking and Handover

`tracking_plan` enumerates two- and three-boat combinations and bounded angular
observation slots. It computes real Dubins transits, iterates arrival-time
prediction, checks passive capability, range and endurance, and scores geometry,
arrival delay, arrival spread and search-resource cost. Fleet selection seeks
to retain a feasible scanning resource. Slot assignment is not tracking success.
Transit validation also propagates the measured constant-velocity estimate and
its covariance over the actual Dubins travel time. Segment clearance includes
the unchanged observed-contact safety envelope plus a planning buffer; future
observations are not assumed to reduce uncertainty before they occur.

The runtime distinguishes transit, acquisition, tracking, degradation and
reacquisition. Transit must actually reach its reference before a member can
qualify for cooperative tracking. Current success conditions require at least
two in-position team observers with fresh passive measurements, bearing
geometry at least 0.3, uncertainty at most 120 m and a sustained three-second
window. Moving-center orbit control adjusts relative phase while keeping
forward speed and bounded curvature. Active reacquisition does not accumulate
continuous passive-tracking time.

New tracking plans declare `acquisition_mode=active_until_cooperative_geometry`
and list their active-capable members in `acquisition_requirements`. A newly
formed team needs at least two active-capable observers; the planner filters
ineligible combinations before scoring and evaluation rechecks capability.
Its capable boats use active sensing during
transit and acquisition. The whole group switches to passive only after two
in-position team boats supply actual accepted active observations in the same
sensor cycle, with geometry at least 0.3 and uncertainty at most 120 m. That
active cycle earns no passive tracking time. Three subsequent fresh passive
seconds are still required for tracking success. Sensor mode is public telemetry,
not inferred from task labels. A passive-only third member remains passive.

Adding relief to an already established passive group does not activate its
sensors or the existing team. This exception requires two retained observers
outside the newly activated membership; replacing both observers with fresh
transits starts a new active bridge. The internal relief planner explicitly
sets `require_active_acquisition=False`; ordinary tracking planning defaults to
new-team acquisition. Lost-contact reacquisition resets the active
bridge. Each observation cycle captures sensor modes before transitions and
uses those same modes for both measurements and the coverage ledger. Tracking
boats do not gain search-region ownership from active acquisition. In the seed-42
23-second sensor-outage regression, measured recovery takes 34 seconds; the
test's 45-second bound applies to that scenario, not arbitrary maneuvers.

`handover.py` implements bounded standing-policy relief for a two-boat team.
It plans an incoming third observer and repairs search responsibilities before
committing. The departing boat remains assigned until incoming and surviving
members supply fresh, generation-matched passive observations with sufficient
geometry for three seconds. A three-member team never admits a fourth member;
it currently falls back to reserve-triggered exit and reported degradation,
not guaranteed uninterrupted three-boat relief.

Handoff attempts increment only when a real relief assignment commits, and are
checkpointed alongside completed handoffs. Success rate is completed/started
as a percentage, including in-progress attempts in the denominator; no attempts
means a null rate. Legacy checkpoints recover the denominator only from a
complete contiguous event history. Truncated histories remain unknown for that
episode instead of inventing a denominator. The UI distinguishes null from zero.

## Energy, Authorization and Persistence

Remaining range decreases with actual movement. Default capacity is 18000 m
and configured exit reserve is 1200 m. Exit preparation considers proximity to
the outer boundary, reserve and a feasible forward route. Authorized boats must
cross that boundary before replacement. Entry positions reserve space against
other boats and earlier replacements in the same tick. The old slot's
generation increments, energy resets and its trail ends; unsafe entry or failed
search repair causes a protective pause without publishing seven boats.

Candidates are immutable proposals. Submission and delayed approval validate
member assignments, generations and signatures of affected region ownership;
unrelated region changes do not automatically expire a plan. Standing-policy
energy rotation and local repair are checked in code. Request mode requires new
plan approval; Assisted mode compares a task-type base plus energy and contact
uncertainty terms against 0.55; Full mode bypasses individual approvals, not
hard constraints. Risk scores are heuristics, not accident probabilities.

SQLite checkpoints preserve coverage, contacts and covariance, observation
cursors, energy, generations, assignments and transition state. Recovery does
not simulate offline wall time. Replay frames include their own latest 30
messages and are compressed individually with zlib; legacy JSON text frames
remain readable. This is bounded historical context, not full conversation
archival or deduplication across frames. PI session persistence is separate
from authoritative simulation state.

## PI and UI Integration

The worker registers ten mission tools plus restricted `read`, which can access
only the trusted mission Skill and its five listed references. It uses SDK
resource discovery and actual file reads, not an assumed implicit load of every
reference. Native public message, tool, retry, compaction and queue events feed
the browser; `steer`, `followUp`, abort and settled/idle semantics remain native
session operations. Hidden reasoning and credentials are not public event data.

Acknowledged feedback remains persisted after native enqueue until the worker
reports completion after the session settles. Failure or lease expiry requeues
unconfirmed feedback; a recovered instruction may therefore repeat. Each item
has at most three recovery runs, then its original message is marked failed.
Retries retain message identity and precede newer queued input. This is bounded
retry, not guaranteed completion or exactly-once processing. Mission submissions
retain idempotency for the same `command_id`, not a general guarantee against
new decisions made from repeated feedback. Pending and accepted feedback share
a 20-item limit per run. Explicit cancellation clears both queues for that run.

The configured default model is `LongCat-2.0`. A worker job has a 180-second
deadline, a 24-call mission-tool budget and a 12000-public-event limit. Active
run leases expire after 15 seconds without renewal. Worker offline display uses
a separate 30-second silence threshold to allow the healthy 15-second success
cooldown; that display grace does not extend tool authorization. Model
completion does not stop authorized simulation. The UI places Conversation/Data
on the left, the map on the right, and Timeline/Agent/Metrics below, with retained
scene editing, planning, approval, replay and export entry points.

## Provenance

The Dubins analytic kernel is adapted from MIT-licensed PythonRobotics commit
`b2020cd613d0709e9c0f38a7579f1a681cf7a227`, retrieved 2026-09-27.
`vendor/LICENSE` and `vendor/PROVENANCE.md` retain licensing and exact source
attribution. Partitioning, team scoring, relief and joint-control policies are
project-specific engineering heuristics, not reproductions of a cited paper's
complete algorithm or performance.

## Verification Commands and Evidence Limits

From the repository root:

```sh
PYTHONPATH=tools python -m pytest tools/tests -q
node --import ./packages/coding-agent/src/experimental/source-resolver.ts --test tools/pi/*.test.ts
npx tsc -p tools/pi/tsconfig.json --noEmit
npm run check
```

Run the complete UI state/component test set from `ui/`:

```sh
cd ui
node --test src/state/*.test.js src/renderer/*.test.js
cd ..
```

The offline v2 fleet acceptance driver does not call a model:

```sh
PYTHONPATH=tools python tools/acceptance/v2_acceptance.py --sim-seconds 14400 --wall-seconds 1800 --output outputs/v2-acceptance.json
```

Read `passed`, `acceptance_checks`, failures, pauses and raw metrics in the JSON.
The exit code only indicates whether the requested run completed; a short run
may exit successfully while correctly reporting `passed: false`. Protective
pause is failure for uninterrupted-run acceptance, not evidence of completion.
The driver checks ownership counts, motion, generations, energy and tracking
conditions, but does not itself establish provider-online operation or all UI
and storage-growth requirements.

Paid-model checks are separate, explicitly authorized operations:

```sh
python tools/acceptance/model_acceptance.py
python tools/acceptance/model_acceptance.py --fleet
```

The second command plans and submits into the connected live episode. It checks
real tool receipts and fleet ownership, not long-duration tracking. Unit tests
with injected energy, generations or observations verify contracts but cannot
prove natural fleet endurance or successful live handover. Exact run evidence
belongs in acceptance reports; the offline pass above does not establish
provider-online acceptance.
