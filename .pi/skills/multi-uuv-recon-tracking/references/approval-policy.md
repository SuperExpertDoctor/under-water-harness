# Approval and Standing Policy

Request mode waits for a human on new plans or changes outside prior authorization. Approved plans and standing rules continue during model waits. Assisted mode executes only permitted routine changes and requests approval for higher risk or expanded scope. Full mode still enforces geometry, energy, generations, partial observation and stop instructions.

Set `standing_policy: true` in `plan_search` only when the user asks to include bounded energy rotation and local repair. This creates proposed authorization inside the immutable plan, not immediate authority. Explain affected boats, regions, energy reserve, observation expectations and coverage cost from tool results.

Chat, annotations, feedback and a permission-mode change do not approve a specific immutable plan. Only the human approval API can approve. No PI tool can approve, change mode or resume a protection pause. Rejected/stale plans must not be revived.

Stopping generation cancels subsequent submissions from that run but leaves already authorized simulation tasks intact. Pausing simulation is a separate human control.
