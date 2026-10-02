"""Plugin contract for the UUV control stack.

A plugin is a single ``*.py`` file dropped into ``tools/uuv_game/plugins/``.
The loader imports every module whose name does not start with ``_`` and
registers it when the module defines a ``PLUGIN`` dict. Copy
``plugins/_template.py`` as a starting point.

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
    edges       outgoing connections; each entry is ``("from_id", "to_id")``
                or ``{"from": ..., "to": ..., "always_active": True}``
    owns_stages pipeline slot names this plugin owns. The runtime skips a
                slot only when *every* owning plugin is disabled (core
                plugins cannot be disabled). See STAGE_SLOTS below.

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
