# UUV Recording and Consistency Fixes

## Objective

Record the complete live mission-control interface for long-running sessions to an MP4 under `outputs/`. Recording must continue when the operator closes their own browser tab, until stopped from a later tab or until the backend process exits. Repair the five previously reviewed backend/frontend inconsistencies and the stale sonar regression test in the same delivery. Do not change simulation behavior except the explicitly identified zero-speed target fault.

## Recording architecture

- The API owns one recording session at a time. A local headless Chromium browser opens the same live UI URL as the operator, at a fixed desktop viewport. Playwright writes its browser video to a temporary file. The recording process is independent of the operator's tab, but not of the API process or the UI service.
- The launcher supplies the actual local UI origin to the API, including when the preferred UI port is taken. The backend never accepts a URL or output path from an API caller. Resolve the output directory under the project root, generate a unique filename there, and prohibit concurrent starts. A missing browser binary or encoder returns a clear error without claiming a recording began.
- A start endpoint creates the recorder and returns its status; a stop endpoint finalizes the browser video, then uses ffmpeg to produce an H.264/yuv420p MP4 in `outputs/`. Status distinguishes starting, recording, finalizing, completed, and failed, including elapsed time, output basename when completed, and errors. Conversion is asynchronous so a long file does not block the HTTP request; only a verified completed file is reported as saved. Keep the source recording if MP4 conversion fails, for recovery; remove it only after successful conversion. A backend shutdown attempts to finalize an active recording, but backend crashes/restarts do not guarantee a complete video.
- The control strip gets a record/stop icon button with accessible name and title, a visible live/processing/error state, elapsed time, and the saved filename or failure message. The UI reads server status on mount and polls while active, so reopening the page reconnects to the same recording. Existing replay-only map export stays unchanged. Recording never pauses or changes the mission.

## Consistency fixes

- Accept zero target speed as a stationary adversary: keep its position and heading unchanged for a tick, without passing zero speed to the movement integrator. Preserve speed validation for negative or out-of-range input.
- On an episode reset, do not allow an HTTP state request begun under the previous episode to overwrite a newer WebSocket snapshot. Keep the existing episode/frame ordering for normal same-episode traffic.
- Build both configured WebSocket URLs from the configured origin while retaining each requested path (`/ws/live`, `/ws/state`), instead of routing both to the same configured stream.
- Compute information white/gray/black counts from searchable cells on the backend, alongside the denominator used for coverage. Display those counts on the UI so blocked cells cannot leak into the presented totals.
- Clarify the pending-approval message: a new plan awaits approval, while already authorized execution and simulation can continue.
- Update the fixed-cell sonar test to use a cell actually covered by the side-scan geometry, while retaining an assertion that an uncovered cell stays unscanned.

## Validation

Use deterministic backend tests for stationary target ticks, recording start/stop/status/error/concurrency and output location, and information counts; frontend tests for episode race, configured socket routes, recording status and blocked-cell counts. Use a short real local-browser recording smoke test to confirm UI frames, browser-tab independence, and a playable MP4 when the required tools are available. Run repository checks and focused tests under the repository's command restrictions. Do not claim recording persists through backend restarts or server failure.
