# Search Responsibilities

Omit `members` from `plan_search` for one atomic fleet-search candidate. K available search boats receive K connected regions with one owner each. Explicit members select up to eight boats, not a three-boat tracking team. Zero available boats means zero regions and a coverage backlog.

Use `partition_search_area` to inspect connected candidate regions and workload, or `compute_task_allocation` to compare candidate assignments. Neither executes. `plan_search` includes executable routes and region ownership; evaluate then submit its result ID once.

Preserve global scan timestamps when changing owners. Tracking, exit and return change the number of search regions. Coverage repair belongs to the same approved composite plan. Never imply that a candidate has already scanned its area. Inspect completion and scan ages from authoritative state.
