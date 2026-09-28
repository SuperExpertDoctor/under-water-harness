# Information Map and Full-Area Coverage Acceptance

This revision preserves the ocean bitmap and replaces large ownership fills with
thin responsibility boundaries and continuous per-cell information tint.
Credentials remain in the ignored runtime configuration, not in this document.

## Behavior

- Scan freshness is `2 ** (-age_s / 180)` after an actual valid active scan;
  unseen or obstacle cells are zero. Re-observation refreshes the timestamp.
- Target evidence is separate from scan coverage. Accepted localized estimates
  produce a covariance-oriented neighborhood with a 150 m display-width floor.
  Evidence age has a 60 simulation-second half-life; growing uncertainty spreads
  the field and reduces its peak. An unlocalized bearing produces a range-limited
  angular fan, not an invented point location. The field is a display-confidence
  score, not a normalized probability or Shannon information measure.
- Rendering never samples hidden target truth. Pause freezes simulation-time
  decay; repeated reads cannot refresh information. HTTP, WebSocket, checkpoint
  restoration and replay transport the same fields.
- Region ownership and observation are distinct: thin colored boundaries identify
  responsibility; subdued cells identify acquired information. A dark cell can
  still belong to an assigned region. Solid boundaries do not fade with evidence;
  ownership labels use the assigned cell nearest the region centroid rather than
  the first cell at the top. Region-table means use the same authoritative fields.
- Automatic global coverage includes active-sensor-capable available boats with
  adequate exit reserve. Tracking, tracking transit, other main tasks and exiting
  boats are excluded. Travel to an assigned coverage region does not remove its
  owner. Each available search boat owns one connected region; their disjoint
  union covers all searchable cells (1,584 of 1,600 in the default obstacle map).
- Temporary boat collision envelopes affect routes, not permanent ownership.
  Role changes retain scan timestamps and repair ownership. No eligible boat
  yields an explicit infeasible global request, while authorized last-boat exit
  can relinquish all regions without triggering a false safety pause.
- Automatic UI search omits local bounds and member overrides; local-boundary
  inputs are available only for explicit scoped searches.

## Review Fixes

Independent UI review identified mobile map crowding and a tooltip covering the
selected right-edge cell. The mobile summary/timeline are now compact; the map
grew from approximately 229 to 329 CSS pixels at a 390-pixel viewport. Desktop
tooltips flip left; mobile uses the persistent numeric reading below the map.
Scale markings have a light backing. Keyboard selection and numeric legends
avoid relying on color or pointer hover alone.

Review also found that automatic search still displayed ignored local-boundary
inputs; these inputs and their validation now apply only to explicit local mode.
Vite dependency caches are separated by backend port so middleware tests cannot
invalidate the live browser's optimized modules.

Independent backend reviews found no blocking issue in the information fields
or global-coverage filtering. Legacy contacts without retained active samples or
localization provenance conservatively show bearing fans until a new accepted
active observation establishes provenance.

## Verification Status

- Real LongCat retry passed in 740.707 wall seconds:
  `../outputs/v4-longcat-retry-acceptance.json`. Native skill loading, atomic
  full-area planning, approval isolation, automatic two-boat tracking selection,
  operator approval, effective tracking and annotation preservation were checked.
  There was a temporary observation loss before recovery; 32 effective tracking
  simulation seconds were accumulated, not uninterrupted tracking throughout.
- Live ownership covered all 1,584 searchable cells uniquely with eight search
  boats, and still covered all 1,584 after two boats transferred to tracking and
  six search regions remained. Obstacles account for the other 16 cells.
- `../outputs/v4-information-live.json` passed: observed scan decay, refresh and
  target-neighborhood evidence from public live frames, without injecting truth.
- `../outputs/v4-dual-longcat.json` verifies both configured worker credentials
  were loaded (booleans only) and 55 real enemy parameter-setting receipts.
- `../outputs/v4-centered-browser.json` and `../outputs/v4-tracking-browser.json`
  passed browser-error, nonblank canvas, assets, layout and selection checks.
  Independent UI review accepted the centered labels and complete boundaries on
  desktop and mobile, including a genuinely discovered target.
- 297 Python tests (176.79 seconds), 58 UI tests, 19 native PI tests and
  `npm run check` passed. The native test
  runner emits Node's known experimental MockTimers warning, not a test failure.
- `../outputs/v4-interaction.json` passed the actual browser operator workflow:
  HTTP/WebSocket field parity, keyboard values matching backend cells, native
  text-selection annotation with a real LongCat response, non-mutating full-area
  candidate calculation, and start/motion/pause controls. Desktop, 390/375-pixel
  mobile and 844-by-390 landscape screenshots were captured; no browser errors
  or horizontal overflow were reported. Services remain available with the
  simulation paused for inspection at `http://127.0.0.1:5186`.

The first real run failed to discover the target within its deadline; its report
is retained as `../outputs/v4-longcat-acceptance.json`. The initial browser gate
also exposed incomplete ownership from explicit legacy strip planning. Tool
guidance and acceptance now require the actual submitted automatic connected
planner and complete unique ownership, rather than counting a failed tool call.
These failed attempts are not reported as passes.

The separate historical V3 acceptance document remains unchanged. No permanent
tracking guarantee, infinite-runtime proof, or exhaustive verification of every
possible operator sequence is implied by a finite demonstration run.
