# V2 Acceptance Record

Date: 2026-09-27. Baseline: `46ca1ec` on `feat/uuv-mission-control`.
This records uncommitted v2 work. No new commit was requested for this turn.

## Current Verdict

Most implementation and targeted integration checks are complete. The original
three-boat transit failure is fixed by measured active acquisition before passive
tracking. A pre-fix real-model endurance run kept moving but failed to reestablish
tracking after the original team left. Critical-event scheduling was corrected;
fresh final offline and real-LongCat runs are required. This document does not
yet declare full acceptance.

## Verified Integration

- Repository `npm run check`: passed, with no formatter fixes in the final run
  preceding endurance tests. Four repaired catalog/transport test files: 65 pass.
  Production provider catalogs were not fabricated to satisfy stale test IDs.
- Python: final expanded suite, 246 tests passed in 126.77 seconds, including
  genuine three-boat transit and active-acquisition regressions. PI SDK tests:
  13 pass. Scoped PI TypeScript: exit 0.
- UI: 22 state/component tests and 2 map-label regression tests pass. Native
  PI events, sanitized Markdown, annotation references, read-only replay,
  generation boundaries and mobile label avoidance are covered.
- Credential scan: 2128 tracked or nonignored files scanned; zero occurrences
  of the configured LongCat key. Credentials remain in the ignored local file.

The initial Python invocation omitted `PYTHONPATH=tools` and failed collection;
the correct documented invocation was rerun successfully. Failures were not
removed from tests to obtain these results.

## Requirement Checks

| Design Check | Evidence / Current Scope |
| --- | --- |
| A01 observation isolation and repeated reads | Observation/runtime/event tests; stable sample cursor; normal frames exclude scene truth |
| A02 bearing geometry and filter behavior | Wrapped angles, passive-only updates, geometry-sensitive control and covariance tests |
| A03-A04 dynamic ownership and repair | Eight-to-six search regions, local merge/split, unaffected ownership and preserved coverage tests |
| A05 real transit before tracking | Offline scenario and real LongCat observed acquisition, not just accepted plans |
| A06 two/three observers | Two-boat and explicit nearest-three genuine transit scenarios pass after active-acquisition fix |
| A07 handover | Real incoming transit and sustained fresh passive observations; three-member cap |
| A08 boundary turnover | Natural rotation evidence plus simultaneous-entry/generation/energy regression tests |
| A09 infeasibility | Rejection and protective-pause tests; no fallback teleport or overlap |
| A10 permissions | Request/Assisted/Full, delayed approval, affected-region signatures and old-generation rejection |
| A11 native PI lifecycle | SDK streaming, queue, settled, abort, compaction/retry projections; bounded feedback recovery |
| A12 lost contact | Actual outage and active reacquisition tests; lost time reported separately |
| A13 browser integration | Annotation delivery, safe Markdown, replay isolation, reconnect/state tests |
| A14 endurance | Fast four-hour probes pass; paced offline and real-model continuation pending |
| A15 browser | Genuine motion and desktop/mobile screenshots pass after label fix |

## Real LongCat Workflow

`../outputs/v2-longcat-acceptance-pre-bridge.json`: baseline passed in 230.134 wall seconds with the
real `LongCat-2.0` worker and public HTTP APIs. It verifies:

1. Actual native Skill read and one atomic eight-boat search submission.
2. Real active detection followed by explicit observation-tool reads.
3. Request-mode candidate leaves existing execution unchanged before approval.
4. Operator approval activates two-boat transit, followed by at least 30 seconds
   of effective passive tracking with actual team bearings and valid geometry.
5. Annotation of a real assistant message is delivered without replacing the
   approved plan; native message/tool/settled receipts are retained.

The harness explicitly pauses for human review and annotation checks. Those
pauses are test-operator actions, not an Agent requirement to freeze simulation.
The first attempt omitted a required dedicated observation-tool receipt; its
failed report remains at `../outputs/v2-longcat-first-attempt.json`. The prompt
was made explicit and the strict receipt assertion was retained on rerun.

After the active-acquisition and event-priority changes, the same five checks
passed in 397.174 seconds (`v2-longcat-acceptance-pre-replan-guard.json`). Final
review then corrected reactivated-member counting and capability filtering.
The fully restarted final version passed all five checks in **248.915 seconds**
(`../outputs/v2-longcat-acceptance.json`), including actual native feedback
delivery. Endurance is assessed separately below.

## Endurance Evidence

Development seed 42 was used while correcting collision-candidate pruning,
low-energy route entry and remaining-coverage handoff failures. Earlier failures
remain in `../outputs/v2-probe-*.json` and are not passing evidence.

After freezing the numerical algorithms, held-out seed 20260927 was selected.
No parameters were adjusted after observing its result.

| Run | Simulated | Wall | Rotations | Handoffs | Effective Tracking | Lost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Development seed 42 final probe | 14400 s | 197.56 s | 24 | 8 | 10577 s | 460 s |
| Held-out 20260927 fast probe | 14400 s | 241.77 s | 24 | 7 | 10744 s | 475 s |
| Held-out 20260927 paced baseline, pre-bridge | 14400 s | 1800.007 s | 24 | 7 | 10744 s | 475 s |
| Real LongCat baseline, pre-priority | 10409.8 s advanced | 1803.074 s | 16 total | 2 total | 2599 s total | 5947 s total |

The old online harness reported `passed` because it required repeated real-model
completions and motion, but did not require tracking recovery after team release.
The report remains at `../outputs/v2-longcat-soak-pre-priority.json`; this is **not
full product acceptance**. Its 46 completed model runs exposed critical contact
events being coalesced into routine checks. The strengthened harness now requires
natural rotations, effective tracking, and recovery whenever all trackers leave.
The paced baseline is `../outputs/v2-acceptance-heldout-20260927-pre-bridge.json`.

Both completed fast probes performed 72000 tick audits with no pauses or
invariant failures. They correctly report `passed: false` because they did not
run for 30 wall-clock minutes. Search ownership tracked the available search
fleet; the development run observed every count from one through eight.

The offline driver exercises real algorithms, sensors and runtime but no model.
Its SQLite samples measure checkpoint/event load, not API replay-frame writes.
The separate online continuation measures the actual API, PI worker and Vite
process RSS and live database size, including the API's replay persistence.
Online sampled checks are not substitutes for the offline per-tick audits.

## Browser Evidence

`../outputs/v2-browser-acceptance.json` verifies desktop 1440x900 and mobile
390x844: nonblank canvas pixels, decoded map/UUV assets, real position and canvas
changes, actual tracking bearings, metrics and rendered-text annotation drafts.
No browser console/page errors or page-width overflow were observed.

Screenshots: `../outputs/v2-accepted-desktop.png`, `v2-accepted-data.png`,
`v2-accepted-metrics.png`, `v2-accepted-tracking.png`,
`v2-accepted-annotation.png`, and the three `v2-accepted-mobile*.png` files.
The paused-only first browser attempt did not claim movement and remains
separately recorded. The passing motion probe records explicit operator
start/pause actions. Map-label overlap found during visual inspection was fixed
with regression coverage and fresh screenshots.

## Scope and Limits

- This is a 2D game: forward 4 m/s motion, minimum 60 m turn radius, eight own
  entities, at most three tracking members. No real equipment or acoustics.
- Passive observations contain bearings, not target truth. The EKF, team
  geometry and finite-horizon controller are simplified engineering methods,
  not reproductions of full paper results or global-optimality guarantees.
- Target loss is measured, not hidden. Scan completion and assigned tracking
  are not evidence of effective continuous observation. Partial low-energy
  patrols explicitly defer unfinished work without clearing coverage history.
- Automatic relief supports a two-boat team admitting one replacement. A
  three-boat team never admits a fourth and may degrade during energy exit.
- Feedback recovery is bounded to three recovery runs, preserves original
  message identity/order, and then marks failure. It is not exactly once.
- Normal frames and model tools exclude target truth. Explicit human scene
  editing is a separate interface. Services are local-only, not public auth.
- Replay retains 7200 frames per episode and eight episodes, with 30 messages
  per frame. Historical receipts and PI sessions still require disk monitoring.
  No finite test establishes unlimited lifetime or zero resource growth.

## Reproduction

Run from the repository root unless specified otherwise:

```sh
PYTHONPATH=tools PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tools/tests -q
node --import ./packages/coding-agent/src/experimental/source-resolver.ts --test agent/*.test.ts
npx tsc -p agent/tsconfig.json --noEmit
npm run check
```

From `ui/`: `node --test src/state/*.test.js src/renderer/*.test.js`.

```sh
PYTHONPATH=tools python tools/acceptance/v2_acceptance.py --seed 20260927 --sim-seconds 14400 --wall-seconds 1800 --output outputs/v2-acceptance-heldout-20260927.json
python tools/acceptance/v2_live_acceptance.py --reset --deadline 600
python tools/acceptance/v2_live_soak.py --seconds 1800
python tools/acceptance/v2_browser_acceptance.py --api http://127.0.0.1:8773 --ui http://127.0.0.1:5180 --measure-motion
```

Live scripts alter the dedicated demo episode and use the real paid model.
`--reset` explicitly destroys the current episode's active execution; use it
only for a dedicated acceptance run. Online continuation requires a paused,
already-approved fleet, explicitly selects Full autonomy, and pauses on exit.
Service URLs are discovered from `outputs/runtime/services.json`; browser flags
must match the currently running ports. Do not run competing operator harnesses
against one live episode simultaneously.
