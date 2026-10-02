# Passive Cooperative Tracking

Normal tracking observations are noisy bearings from known observer poses, not target truth, direct range or passive target coordinates. Use the contact estimate, covariance, age and observer geometry. Re-reading observations cannot improve an estimate without a new sample.

For confirmed `contact_id`, omit members from `plan_tracking` to select a feasible two/three-boat team; explicit members must contain two or three distinct available boats. The candidate includes member-specific Dubins transit, observation positions, energy feasibility, acquisition conditions and repair of remaining search coverage.

Distinguish assigned, transit, acquisition and effective tracking. Effective tracking requires fresh observations from the assigned team and adequate geometry over the configured window. An incidental observation from a search boat does not establish team tracking.

A new team's candidate declares `acquisition_mode: active_until_cooperative_geometry`. It requires at least two active-capable members; all members must support passive tracking. Its active-capable members use actual noisy active observations during transit and acquisition. The backend switches the team to passive after at least two assigned members have left transit and supplied accepted active measurements in the current sample cycle, with geometry quality at least 0.3 and estimated uncertainty at most 120 m. Effective tracking additionally requires three consecutive simulated seconds of fresh passive team observations satisfying the same geometry and uncertainty conditions. Active transit, acquisition and reacquisition never earn passive tracking credit. Report each boat's actual `sensor_mode`, not a mode inferred from its assignment.

An incoming member of an already established passive team stays passive during handover; adding it does not restart active acquisition for that team. These transitions belong to the approved candidate and backend controller, not extra commands the model should invent.

For handover, at most three boats belong to a tracking team. A three-boat team cannot add a fourth under a standby label. Missing observations produce prediction then degradation/loss. Reacquisition is active search around the last estimate and uncertainty; it is not continuous passive tracking.
