# UUV Mission Control Implementation Plan

> Historical plan for the first implementation only. The checked items below do not cover the [v2 revision design](../docs/superpowers/specs/2026-09-27-uuv-mission-control-v2-design.md). V2 implementation and acceptance have not started; its detailed implementation plan follows written design approval.

> Execute with subagent-driven-development and test-driven-development. No commits without user request.

Goal: complete the existing UI integration with real Python algorithms, persistent mission control, three permission modes and PI SDK using LongCat-2.0.

Architecture: Python owns simulation, plans, permissions, SQLite and the public HTTP/WebSocket API. A separate Node PI SDK worker consumes serialized decision jobs and calls only the nine registered mission tools. This small deployment refinement avoids a second HTTP proxy and keeps model latency outside simulation.

## Constraints

- Eight UUVs, maximum three per team, SI internal units, fixed positive speed during active navigation, bounded curvature, no instant turns.
- Candidate computation never executes a mission. Approval is bound to immutable plans; all execution passes the same gate.
- Request/Assisted/Full permissions, explicit human stop, approved safety pause, real model failures shown without simulated LLM replies.
- Existing ui assets, scene editor, intent editor, replay and MP4 export retained and connected to documented contracts.
- No target truth in agent tool state. No API keys in versioned files, UI, logs or tests.
- Work in the requested checkout (untracked user UI lives here), on a feature branch. Preserve unrelated changes.

## Task 1: Algorithm Core

Files: tools/uuv_game/algorithms/{__init__,motion,planning,allocation,coverage,tracking}.py, tools/uuv_game/vendor/, tools/tests/test_algorithms.py.

Public interfaces (plain serializable dictionaries; x/y in meters, heading radians):

```python
integrate(pose: list[float], speed: float, curvature: float, dt: float) -> list[float]
plan_path(start, goal, radius=60.0, obstacles=None, bounds=(0,0,4000,4000), step=5.0, budget=3000) -> dict
# {status, points:[[x,y,heading],...], length_m, algorithm, diagnostics}
allocate_tasks(uuvs: list[dict], tasks: list[dict]) -> dict
# uuv: {id,pose:[x,y,h]}, task:{id,center:[x,y],size:1..3,priority:1..10}
# {status,teams:[{id,task_id,members:[id]}],algorithm}
plan_search(uuvs, bbox, radius=60.0, obstacles=None, bounds=(0,0,4000,4000), spacing=450.0) -> dict
# {status,routes:{uuv_id:points},algorithm,diagnostics}; loops with valid connectors
tracking_control(pose, estimate, radius=60.0, speed=4.0, slot=0) -> float
# estimate:{x,y,vx,vy}; curvature command, distance-band orbit not stationary trailing slot
follow_path(pose, points, radius=60.0, lookahead=40.0) -> float
path_safe(points, obstacles, bounds, margin=8.0) -> bool
# circle obstacles {x,y,radius}; swept segments with conservative curvature margin
```

- [x] Write test_algorithms.py first: straight and arc integration, finite pose/radius validation, six Dubins families/endpoints, obstacle detour, thin-obstacle swept detection, allocation uniqueness/max-three, search continuity, bounded tracking for stationary/slow target.
- [x] Run `PYTHONPATH=tools python -m pytest tools/tests/test_algorithms.py -q`, record RED.
- [x] Vendor pinned MIT PythonRobotics Dubins subset, license and provenance; implement bounded forward obstacle fallback and heuristic coverage/control with SciPy allocation. Fail explicitly on infeasible/budget exhaustion.
- [x] Run the same tests GREEN and record in tools/algorithm-report.md.

## Task 2: Runtime, Permissions and Public API

Files: tools/uuv_game/{config,store,runtime,api}.py, tools/tests/test_runtime.py, tools/tests/test_api.py, tools/requirements.txt. Permission logic remains together with atomic runtime state changes.

- [x] Test 8 initial UUVs; observation-only state; simulation progresses pending approval; reject duplicate team members, stale episodes/versions; idempotent submit; restart preserves pending approvals and commands; human stop invalidates execution.
- [x] Run focused pytest RED, then implement fixed-step simulator, measured observations, reusable plan/tool service, SQLite persistence, validated control and explicit safety pause.
- [x] Implement every route in ui/用户手册与接口说明.md section 8, including binary WebM to MP4 with ffmpeg capability detection; HTTP errors never fake success.
- [x] Add chat, permission/approval endpoints, jobs and internal PI worker queue. Public UI authenticated using same-origin cookie; internal worker uses a distinct bearer token and only tool/event endpoints.
- [x] Run focused tests GREEN, then schema/endpoint integration tests including WebSocket, reset, replay, scene/intent command receipts and malformed inputs.

## Task 3: PI SDK and LongCat

Files: tools/pi/{worker,extension,config}.ts, tools/pi/*.test.ts, .pi/skills/multi-uuv-recon-tracking/SKILL.md, tools/scripts/run.py.

- [x] Verify provider docs and local SDK types. Test configuration and tool allowlist without model calls.
- [x] Register nine typed tools through an Extension; use createAgentSession, no coding/shell tools. Load mission Skill explicitly into trusted session instructions.
- [x] Persistent serialized worker, bounded turns, heartbeat, cancellation, periodic/event wakeups and no hidden reasoning exports.
- [x] Test normal/failure/cancel transitions without paid calls, then run separately authorized LongCat integration (real tool call + receipt), redacting credentials.

## Task 4: Full UI Connection

Files: ui/src/App.jsx, components/{MissionControl,AgentPanel,BottomDrawer,RightSidebar}.jsx, hooks/useMissionControl.js, api/missionApi.js, state/missionState.js and focused tests.

- [x] Test state merging/reset/reconnect and readonly commands before implementation.
- [x] Connect toolbar run/pause/stop/reset/modes; chat, approvals, tasks, Skills and algorithm operations; default bottom timeline; preserve map/scene/intent/replay/export.
- [x] Real telemetry uses backend information matrix. Offline demo is explicit and has eight boats. Highlight teams/candidate routes without ground-truth leakage to PI.
- [x] Run component/state tests and browser desktop/mobile workflows; screenshots and Canvas pixel/motion checks.

## Task 5: Acceptance and Handoff

- [x] Run all tools tests, `npm run check` (record upstream model-catalog type failures separately), UI tests and browser checks.
- [x] Run deterministic search/detect/track/lost/reacquire, permission, obstacle and model-disconnection scenarios, accelerated four-hour simulation; report actual duration/results.
- [x] Finish 30-minute wall-clock soak and record actual metrics: 1800.001 wall seconds, 17999.8 simulation seconds, nine closed search loops, checkpoint recovery, stable sampled peak RSS.
- [x] Independent spec/code review; fix important findings and rerun affected tests.
- [x] Write tools/README.md and tools/ACCEPTANCE.md with commands, dependency/protocol provenance, results and limitations. Start local services and provide URL. No commit.

## Progress

- Tasks 1-5 complete for the documented demonstration scope. Python97/97, PI4/4, UIstate7/7 and final desktop/mobile browser workflows passed. Actual LongCat produced and submitted three disjoint fleet plans; a model-generated Request plan was approved and executed in the browser. Independent review findings have regression tests and fixes. The 30-minute wall-clock soak completed. See ACCEPTANCE.md for exact scenario boundaries, measured results and the repository-wide Fireworks catalog/type-check exception.
