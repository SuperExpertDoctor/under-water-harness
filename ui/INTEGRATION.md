# Mission UI Integration

The existing App, CanvasMap, RightSidebar, BottomDrawer and PlaybackBar remain the application shell. New mission controls and four sidebar views connect to the Python gateway without browser model credentials.

## Connected Flows

- Toolbar: start, pause, immediate human stop, confirmed reset, Request/Assisted/Full permission selection. Simulation and PI status are separate.
- Conversation: real persisted messages, queued/running jobs, cancellation and backend model errors.
- Approvals: immutable plan identity, members, risk, reason, expiry, fallback, preview, approve/reject and plan history. The authorization envelope explicitly includes transit (`execution_domain`, `domain_policy`); it is not represented as only the search box.
- Tasks: select up to three members, search bounds in meters or map selection, tracking confirmed contacts, allocation, compute candidate, evaluate, submit, assemble, rename, preview.
- Skills: backend catalog, execute, active-run cancellation.
- Timeline: visible by default, event deduplication and expandable tool/approval data.
- Map: existing assets and layers retained; candidate routes, team rings and observed-contact uncertainty added. Candidate SI coordinates convert to backend cell-center coordinates, including inverted Y.
- Existing intent, vessel/AIS editing, replay and MP4 export remain. Replay disables all mission mutations; switching to an empty replay clears the old live map.

## State And Authentication

`/api/health` runs before mission connections/commands become available and refreshes the HttpOnly same-origin operator cookie. `/ws/live` supplies telemetry; `/ws/state` supplies events, messages, jobs, permissions and plans. Existing ping/pong and reconnect handling remain. A 2.5-second HTTP snapshot poll recovers state and provides telemetry if a WebSocket is unavailable.

Every JSON mutation is bound to `episode_id`. Binary MP4 export retains the documented WebM endpoint. Episode changes clear candidates, assessments, receipts, task state, selections, cached telemetry matrices, local information model and intent editor state. `information_source: backend` bypasses local matrix recomputation. Offline demo is labeled and contains eight UUVs.

## Verification

- `node --test src/state/missionState.test.js`: 7 passing tests. Tests were written before implementation; initial run failed on the missing module. Covers reconnect deduplication, stale cursors/frames, episode reset, read-only command rejection, coordinate conversion, backend matrix authority and demo count.
- Playwright against the supervised UI at port 5180: desktop 1440x900 and mobile 390x844, no page exceptions or horizontal overflow; rendered Canvas dimensions and colored pixels verified, Tasks controls visible. Screenshots were inspected.
- Browser compute/evaluate/assemble flow: all HTTP 200, real search result succeeded, evaluation valid, task draft created. Candidate geometry visibly renders. Switching to replay disables start/stop and produces no page exception. No model calls were made by this UI verification.
- Full isolated browser acceptance also passed: Request approval/rejection; all mode switches; task assemble/rename; start/pause while approval remains pending; approved UUV-1/2/3 motion with changed Canvas pixels; vessel creation/AIS off/on/deletion; intent creation/edit/cancel; stop/reset with eight initial boats; skill and chat queue/cancel without a PI worker; readonly replay and seek. No browser page errors. Portrait 390x844 and landscape 844x390 had no horizontal overflow and retained at least 100px Canvas height. Closed mobile sidebars are hidden from focus/accessibility navigation.
- Real browser MP4 export and download passed. Latest retained artifact: H.264, 1100x522, 0.846 seconds, 100715 bytes, checked with `ffprobe`. The short clip contains the isolated episode's recorded frames, not a claimed long-duration mission.
- Candidate diagnostics regression: `python ui/scripts/browser_diagnostics.py --url http://127.0.0.1:5180` first reproduced hidden object diagnostics, then passed after the rendering fix. Covers infeasible/timed-out objects and array diagnostics; calculation and candidate responses are intercepted in the browser, so it performs no backend mutation.
- `npm run check`: formatting, pinned dependencies, runtime dependencies, imports, entry graphs, shrinkwrap and install-lock checks passed. The run then failed in pre-existing Fireworks model/compatibility test typings under `packages/ai/test`; the root agent is handling that independent issue.
- UI direct dependencies pinned to the versions already installed; `npm install --package-lock-only --ignore-scripts` completed with zero audit vulnerabilities. No production build or full test suite was run.

## Reproduce Browser Acceptance

Use an isolated temporary API database and do not attach a PI worker. The script resets missions and edits the scenario on the supplied URL; it must not target an active operator mission. Start a Python API on an unused port, then start Vite with `VITE_BACKEND_PORT` pointing to it. Run from the repository root:

```bash
python ui/scripts/browser_acceptance.py --url http://127.0.0.1:15190 --output-dir /tmp/uuv-browser-results
```

Requires Python Playwright/Chromium and, for the MP4 assertion, ffmpeg on the API host. The script preserves screenshots, export and JSON results in its output directory. The isolated servers used for this verification were API 18871 and UI 15190; both were stopped after testing.

Retained evidence: [results](../tools/artifacts/browser/results.json), [approval](../tools/artifacts/browser/approval-desktop.png), [moving fleet](../tools/artifacts/browser/moving-desktop.png), [desktop replay](../tools/artifacts/browser/replay-desktop.png), [portrait](../tools/artifacts/browser/replay-390.png), [landscape](../tools/artifacts/browser/replay-844.png), [MP4](../tools/artifacts/browser/browser-export.mp4). Artifacts are ignored by git.

Model-backed chat, eight-vessel long-run safety and wall-clock soak remain owned by the root task's acceptance pass. UI/UX guidance informed visible labels, focus rings, stable toolbar sizing, sticky sidebar controls and touch-friendly task inputs. No commit was created.
