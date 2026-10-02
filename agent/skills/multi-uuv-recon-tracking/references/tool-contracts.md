# Tool Contracts

There are ten mission tools plus one restricted skill `read` support tool. Coordinates are meters, headings and bearings radians, durations seconds. UI grid cells must not be sent as meters.

Read current episode and mission revision first. Candidate tools accept the current snapshot binding. Backend candidates carry versions, assignments and entity generations. No candidate tool executes. `plan_path` is geometry only without terminal continuation.

`plan_search` without members creates one fleet candidate; `plan_tracking` without members selects two/three boats. `partition_search_area` and `compute_task_allocation` are analysis results, not executable plans.

For a successful executable result, call `evaluate_plan(result_id)`, then `submit_mission_plan(episode_id,result_id,command_id)`. Use stable `command_id = episode_id:result_id` for retries. Submit is the only normal execution entry. `pending_approval` is not active; `active` is not completed; a tracking assignment is not effective acquisition. Query `get_action_status(action_id)` when a receipt is ambiguous.

Use observation cursor/limit for pagination. Handle budget exhaustion, infeasibility, stale versions, cancellation, permission denial and service errors separately. Never retry a mutation with a new command ID to hide uncertainty. Reobserve stale state before recalculating. Report protection pauses explicitly and leave recovery to the operator.
