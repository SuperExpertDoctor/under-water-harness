"""Plugin contract for the UUV control stack.

A plugin is a single ``*.py`` file dropped into ``tools/uuv_game/plugins/``.
The loader imports every module whose name does not start with ``_`` and
registers it when the module defines a ``PLUGIN`` dict. Copy
``plugins/_template.py`` as a starting point.

The contract mirrors the pi tool definition (prompt contribution + parameter
contract + sampling/validation constraint + execute body), split the same way:

    model/operator needs to know   -> id, name, desc, snippet, guidelines
    what may connect               -> inputs, outputs (validated enum)
    registration constraint        -> install-time validation (install_plugin),
                                      not free-form acceptance
    does the work                  -> activity(), STAGES, tick_stages()

Required ``PLUGIN`` keys:
    id        unique plugin id (kebab-case), also the file's registry key
    name      display name shown in the plugin library / connection graph
    layer     column depth in the graph layout (int; roots render leftmost)
    color     node accent color, ``"#rrggbb"``
    desc      one-line description of what the plugin does
    inputs    port cardinality: "none" | "one" | "many"
    outputs   port cardinality: "none" | "one" | "many"
    core      True for base control algorithms (never disableable)

Optional ``PLUGIN`` keys:
    snippet     one-line summary shown on the library card (falls back to
                ``desc`` when omitted) — the promptSnippet analog
    guidelines  list of usage-note strings shown on the card's tooltip —
                the promptGuidelines analog
    edges       outgoing connections; each entry is ``("from_id", "to_id")``
                or ``{"from": ..., "to": ..., "always_active": True}``
    owns_stages pipeline slot names this plugin owns. The runtime skips a
                slot only when *every* owning plugin is disabled (core
                plugins cannot be disabled). See STAGE_SLOTS below.

Failure rules (mirroring the pi tool rules — throwing is the only failure):

    - An exception raised inside ``activity()`` or a custom ``tick_stages``
      fn marks *that* invocation failed: it is recorded as a
      ``plugin_hook_error`` event and execution continues. A custom plugin
      bug can never crash the frame or abort the tick.
    - Returning False from a custom stage fn is the declarative way to
      abort the tick (safety pause); do not use it to signal plugin bugs.
    - Builtin core stages propagate exceptions normally — a core algorithm
      fault is a sim fault and stays loud.

Optional module hooks:

    def activity(runtime, ctx):
        Called once per frame to report this plugin's invocations for the
        connection graph. Inside it:
            ctx.hit(subject)   mark the plugin active; subject is a UUV id,
                               CONTACT id, or plan id shown in the node box
            ctx.meta(text)     one status line rendered under the node title
            ctx.L              shared per-frame lists computed by the
                               registry (searching, tracking, exiting,
                               transit_track, transit_other, relief, moving,
                               low_fuel, contacts, tracked, lost, observers,
                               plans, allocated, reacquire_members, owners)

    EDGE_SUBJECTS = {"from_id>to_id": fn}
        Maps a live edge key to the subjects flowing across it. Each fn
        receives ctx.L and returns a list of ids drawn on the edge.

    def tick_stages(runtime):
        For custom (non-core) plugins that want to run code inside the tick
        pipeline. Must return an iterable of dicts:
            {"after": "<slot name>", "fn": callable(rt) -> bool | None}
        fn returns False to abort the tick (same semantics as a safety
        pause); any other return value continues. ``after`` names a
        STAGE_SLOTS entry; unknown names append at pipeline end.

    TOOLS = {"<plugin_id>__<suffix>": {...}}
        Exposes agent-callable tools on this plugin — the plugin-side half
        of pi's ToolDefinition. Every spec dict requires:

            description           model-facing description
            snippet               one-line Available-tools entry
            guidelines            non-empty list of usage rules
            parameters            JSON-schema dict for the tool arguments
            constrained_sampling  {"type": "json_schema",
                                   "strict": "prefer"|"require"}
            execution_mode        must be "sequential" — plugin tools run
                                  under runtime.lock, shared mission
                                  state is queued not raced
            execute               fn(runtime, params, worker=False) -> dict,
                                  always run under runtime.lock

        Optional: calls_model (True => result must carry "usage"),
        errors (documented failure codes). See _template.py for a full
        example.

        Tool names MUST start with "<plugin_id>__" (plugin id with '-'
        replaced by '_') so a plugin can never shadow a builtin tool.

        Install-time review (agent_tools/review.py) gates registration —
        the spec only reaches the catalog when it passes the production
        bar: raising on invalid input (with required params, execute({})
        must raise, not return), explicit execution_mode, json_schema
        sampling constraint, and usage accounting when calls_model is
        set. Failed installs raise ValueError listing every issue.

        Once installed the tools land in agent_tools.catalog() and are
        callable through /internal/tools/<name>; the PI worker's adapter
        turns each entry into a full registerTool() definition, so no TS
        change is needed for the agent to call it.

Runtime surface available to plugin code (the "port" between plugins and
the simulator; plugins must not import MissionRuntime itself):

    state     rt.uuvs, rt.targets, rt.obstacles, rt.contacts, rt.active,
              rt.plans, rt.intents, rt.regions, rt.scan_times,
              rt.vessels, rt.sim_time, rt.frame_id, rt.status,
              rt.standing_policy, rt.config, rt.plugin_states
    actions   rt.event(type, payload)   append a mission event
              rt.queue_agent(text, kind)  enqueue PI-agent input
              rt.pause(reason)          safety-pause the simulation
              rt.tick()                 re-entrant tick (RLock)
              rt.set_plugin_enabled(id, enabled) / rt.plugin_enabled(id)
              rt.plugin_states          enabled map per plugin id

Capability libraries live under ``tools/uuv_game/capabilities/`` and pure
planners under ``tools/uuv_game/algorithms/``; plugin files import them
with relative imports (``from ..capabilities.lifecycle import ...``).
"""

STAGE_SLOTS = (
    "track_leases",      # provisional tracking lease expiry
    "contact_repairs",   # stale-contact repair + pending repartition retry
    "handover_prep",     # graceful tracking handover preparation
    "exit_prep",         # low-fuel exit / replacement preparation
    "motion",            # per-UUV preferred control + integration + interlocks
    "observations",      # sensor observation -> contact fusion + handover finish
    "scene",             # scenario vessel status projection (unowned: always runs)
    "plan_lifecycle",    # plan / intent expiry
    "coverage_review",   # periodic coverage review + overdue-cell replan
)

__all__ = ["STAGE_SLOTS"]
