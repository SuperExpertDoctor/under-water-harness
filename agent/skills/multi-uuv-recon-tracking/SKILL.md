---
name: multi-uuv-recon-tracking
description: "Coordinate the eight-UUV 2D reconnaissance game: search, confirm contacts, track, reacquire lost targets, and submit plans through human approval. Use for mission starts, observations, mission changes, tool failures and periodic monitoring."
---

# Multi-UUV Mission Control

This Skill is workflow knowledge, not an algorithm or authority grant. Python tools compute real results. The backend permission gate decides execution.

## Every Turn

1. Read `get_mission_state`. Use its current episode, observed contacts, active plans and constraints. Never infer target truth from scenario graphics.
2. Preserve valid active plans. On periodic checks with no material change, return a brief status; do not keep replacing plans or asking for duplicate approval.
3. If no mission exists and the user requested fleet search, call `plan_search` without members for ONE atomic fleet plan. Each available search boat owns one connected region; do not submit separate overlapping team plans. Include `standing_policy: true` only if the operator requests bounded energy rotation and local coverage repair. A request for a specific subset uses explicit members (up to eight). Read `references/search.md` for regional changes.
4. For a confirmed contact, call `plan_tracking` with `contact_id` and omit members for automatic feasible two/three-boat selection. The candidate includes transit and remaining coverage repair. Read `references/tracking.md` for acquisition, handover or loss. Pending approval is a normal result, not an error. Never submit the same proposal again.
5. For a lost contact, use the last estimated position and uncertainty to call `plan_search` with `mode: reacquire`. Never request hidden target coordinates. Ensure the selected search rectangle is large enough for forward turns.
6. Call `evaluate_plan` for a successful candidate, then `submit_mission_plan` with its `result_id`, current `episode_id`, and a unique `command_id` formed as `episode_id:result_id`. Keep that ID for retries. Do not submit failed calculations or allocation results as executable plans.
7. Inspect returned status. Query `get_action_status` when necessary. Report approved/active/pending/rejected accurately; submission is not task completion.

## Geometry and Time

All tool coordinates are meters, headings radians. The map is 4000m square; UI cells are not tool coordinates. Each active UUV moves forward at 4m/s, minimum turn radius 60m. `plan_path` is an experiment/geometry tool: executable missions need terminal continuation, supplied by search loops and tracking policies.

Own vehicles cannot hover at a slow target's relative position. Tracking requires passive-bearing observation geometry, feasible transit and sustained team observations. Assignment and transit do not count as effective tracking. Do not command instant turns, reversing, teleportation or unbounded speed.

All `get_mission_state` intent bboxes are meters. Inspect `execution_domain` separately from search bbox and report its actual authorization scope. Search repeats its approved closed route; do not invent a fallback. Read `references/energy-turnover.md` for energy, exits and generations. Missing observations cause prediction, degradation or loss; protection pauses are explicit runtime decisions, not something to infer from missing data.

New or changed active human intents are material changes: consider redirecting an available search team into that intent's bbox, subject to turn clearance, current tracking obligations and the same approval gate. Ignore expired/cancelled intents. Do not automatically enlarge a human's requested search area to hide an infeasible geometry result; explain the limitation instead.

The default mission target is an underwater UUV (`vessel_class: underwater`). Optional scene-editor surface vessels (`type_i`, `type_ii`) may broadcast AIS. Their observations are auxiliary; do not divert the underwater mission to a surface vessel unless the operator asks. AIS-off contacts require genuine nearby sensor observations.

## Permissions and Failure

Request mode requires human approval for new plans. Assisted mode delegates routine low-risk plans; higher-risk tracking/reacquisition can wait for approval. Full mode still enforces hard constraints. You cannot change permission mode or approve your own plan.

Read `references/approval-policy.md` when explaining approvals or standing authorization, and `references/tool-contracts.md` when handling tool fields or failures. Use the actual restricted `read` tool; these references are not implicitly loaded. Tools calculate candidates; only `submit_mission_plan` can submit one for execution. No arbitrary file access or shell is available.

If a calculation times out, report it separately from infeasibility. Retry only with changed input or a reason. If approval is stale, observe again and generate a new candidate. A protection pause is explicit and only humans may resume the simulation. Model waits do not pause existing authorized control.

## Communication

Answer briefly in Chinese without Markdown tables: what changed, which team/target is involved, and the actual result or pending decision. `active` means assigned; vehicles move only when `runtime_status` is `running`. Use measured observations, not imagined detections. No hidden chain-of-thought, credentials, raw provider request headers or invented tool logs.
