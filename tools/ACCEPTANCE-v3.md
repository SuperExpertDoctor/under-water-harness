# V3 Demo Acceptance

Baseline: `c0144b1`. This round implements the approved single-target, eight-UUV, partially observable game and map refresh. Runtime credentials are ignored, permission 600, and never appear in these reports. Both PI worker environments were checked against the configured local key without printing it.

## Verified

| Check | Result | Evidence |
| --- | --- | --- |
| Python regression suite | 262 passed | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=tools python -m pytest tools/tests -q`, 167.47 s |
| Native PI tests | 17 passed | `node --import ./packages/coding-agent/src/experimental/source-resolver.ts --test agent/*.test.ts` |
| UI state and renderer tests | 45 passed | Five `ui/src/{state,renderer}/*.test.js` files |
| PI TypeScript and repository checks | Passed | PI `tsc --noEmit`; root `npm run check`, 1482 files, no fixes |
| Real friendly LongCat workflow | Passed, 862.954 wall seconds | `../outputs/v3-longcat-acceptance.json` |
| Independent enemy LongCat parameter calls | 28 successful receipts at snapshot | `../outputs/v3-dual-longcat.json` |
| Desktop/mobile assets, layout, canvas, selection | Passed | `../outputs/v3-browser.json` |
| Browser movement during live search/transit | Passed | `../outputs/v3-transit-browser.json` |
| Accelerated four-hour simulation | 72,000 ticks, no invariant failures | `../outputs/v3-deterministic.json` |

The real workflow reset a dedicated episode through the operator API, requested an atomic eight-boat search using the native trusted Skill and tools, and observed actual target discovery. Request mode preserved execution until explicit human approval. The selected two-boat team physically transited, lost and reacquired the contact, established passive observations, and accumulated 32 effective tracking seconds. A real annotated response was delivered through PI feedback without replanning or changing the active assignment. Both agents used `LongCat-2.0`; the simulation continued while models ran. No target truth was injected into either model, planning tools, or the public UI.

The workflow intentionally paused for initial planning, human approval dialogue and annotation review. It therefore proves the approval/feedback workflow, not arbitrary moving-plan success under every model delay. Model freshness checks were not weakened. Final cleanup left the mission paused for inspection.

The accelerated deterministic run completed 14,400 simulated seconds in 490.683 wall seconds, with 25 energy replacements and 9 completed tracking handovers from 10 attempts. Fleet count stayed at eight; region/owner counts matched from one through eight eligible search boats; transit, acquisition, tracking and reacquisition all occurred. It used the no-model enemy fallback. Separate parameterized tests exercised continuously active legal enemy parameters at 2.5 and 3 m/s and obtained effective tracking; those are offline tests, not real-provider evidence.

## Screenshots

- `../outputs/v3-desktop.png`: final observed tracking state and native conversation.
- `../outputs/v3-data.png`: boat task/energy/heading/speed telemetry.
- `../outputs/v3-mobile.png`: mobile map and mission timeline.
- `../outputs/v3-contact-desktop.png`: initial confirmed contact before approval.
- `../outputs/v3-transit-desktop.png`: six remaining search owners and tracking transit.

Both supplied hull images retain their aspect ratio and correct forward orientation. The submarine derivative removes only border-connected near-white background; original assets are untouched. Fractional zoom fill edges are snapped to prevent unintended grid seams.

## Review and Limits

Independent UI review found no critical/important issue. Backend review found a legacy-checkpoint migration bug where tracks referenced removed scene targets; a new regression reproduced it, the migration now retires those actions and pending plans while preserving unrelated coverage, and re-review passed.

- The old v2 deterministic script reports overall `passed: false` because this run did not perform its separate 30-minute wall-clock soak gate. `run_completed: true` and all requested simulated-duration/invariant checks passed. No 30-minute live or unlimited-runtime claim is made.
- `v3-reacquire-browser.json` records a movement-check failure because the acceptance workflow deliberately paused for annotation while the browser probe was sampling. Search/transit motion probes passed; final paused-state screenshots were captured without asserting movement.
- Partial observability and adversarial escape can cause real observation loss; permanent tracking is not guaranteed. No safe feasible path still triggers protective pause.
- Completed private enemy session files are capped at 100. Supervisor text logs, historical plans/receipts and friendly session archives still require operational disk monitoring/maintenance. Empty old private episode directories may remain.
- Sprite hit areas remain smaller than their maximum rendered length near the bow; owner swatches and data rows provide reliable alternative selection.
- Services are localhost-only demonstration services, not a public multi-user deployment or real-vehicle control system.
