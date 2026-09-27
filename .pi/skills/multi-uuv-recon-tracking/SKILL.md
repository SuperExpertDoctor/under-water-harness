---
name: multi-uuv-recon-tracking
description: Coordinate the eight-UUV 2D reconnaissance game: search, confirm contacts, track, reacquire lost targets, and submit plans through human approval. Use for mission starts, observations, mission changes, tool failures and periodic monitoring.
---

# Multi-UUV Mission Control

This Skill is workflow knowledge, not an algorithm or authority grant. Python tools compute real results. The backend permission gate decides execution.

## Every Turn

1. Read `get_mission_state`. Use its current episode, observed contacts, active plans and constraints. Never infer target truth from scenario graphics.
2. Preserve valid active plans. On periodic checks with no material change, return a brief status; do not keep replacing plans or asking for duplicate approval.
3. If no mission exists and the user has requested a mission, call `compute_task_allocation`. Use its teams; each has 1-3 distinct UUVs. For each team, call `plan_search` with the returned team bbox in meters. Preserve different sectors; do not assign every team the same full-map sweep. A human request for a specific subset overrides default fleet allocation.
4. For a confirmed contact, choose an available team or intentionally replace a search team and call `plan_tracking` with `contact_id`. Pending human approval is a normal result, not an error. Do not submit the same proposal again.
5. For a lost contact, use the last estimated position and uncertainty to call `plan_search` with `mode: reacquire`. Never request hidden target coordinates. Ensure the selected search rectangle is large enough for forward turns.
6. Call `evaluate_plan` for a successful candidate, then `submit_mission_plan` with its `result_id`, current `episode_id`, and a unique `command_id` formed as `episode_id:result_id`. Keep that ID for retries. Do not submit failed calculations or allocation results as executable plans.
7. Inspect returned status. Query `get_action_status` when necessary. Report approved/active/pending/rejected accurately; submission is not task completion.

## Geometry and Time

All tool coordinates are meters, headings radians. The map is 4000m square; UI cells are not tool coordinates. Each active UUV moves forward at 4m/s, minimum turn radius 60m. `plan_path` is an experiment/geometry tool: executable missions need terminal continuation, supplied by search loops and tracking policies.

Own vehicles cannot hover at a slow target's relative position. Tracking uses distance-band patrol. Do not command instant turns, reversing, teleportation or unbounded speed.

All `get_mission_state` intent bboxes are also meters, already converted by the backend. Inspect `execution_domain` separately from search bbox: it includes the authorized transfer envelope, while tracking currently requests map-wide authorization. Report this scope accurately. Search repeats its approved closed route; do not invent a loiter fallback. Lost tracking triggers a protective pause until a human resumes a newly approved feasible plan.

New or changed active human intents are material changes: consider redirecting an available search team into that intent's bbox, subject to turn clearance, current tracking obligations and the same approval gate. Ignore expired/cancelled intents. Do not automatically enlarge a human's requested search area to hide an infeasible geometry result; explain the limitation instead.

The default mission target is an underwater UUV (`vessel_class: underwater`). Optional scene-editor surface vessels (`type_i`, `type_ii`) may broadcast AIS. Their observations are auxiliary; do not divert the underwater mission to a surface vessel unless the operator asks. AIS-off contacts require genuine nearby sensor observations.

## Permissions and Failure

Request mode requires human approval for new plans. Assisted mode delegates routine low-risk plans; higher-risk tracking/reacquisition can wait for approval. Full mode still enforces hard constraints. You cannot change permission mode or approve your own plan.

If a calculation times out, report it separately from infeasibility. Retry only with changed input or a reason. If approval is stale, observe again and generate a new candidate. A protection pause is explicit and only humans may resume the simulation. Model waits do not pause existing authorized control.

## Communication

Answer briefly in Chinese without Markdown tables: what changed, which team/target is involved, and the actual result or pending decision. `active` means assigned; vehicles move only when `runtime_status` is `running`. Use measured observations, not imagined detections. No hidden chain-of-thought, credentials, raw provider request headers or invented tool logs.
