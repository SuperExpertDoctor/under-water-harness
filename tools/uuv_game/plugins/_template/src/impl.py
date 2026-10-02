"""Internal implementation — split this across as many modules as the
feature needs (``src/motion.py``, ``src/fusion.py`` …); only the package
__init__ re-exported names are the plugin's interface.

Runtime surface available to plugin code (the "port" between plugins and
the simulator) is documented in plugins/contract.py — use the documented
fields/actions; do not import MissionRuntime itself.
"""


def activity(runtime, ctx):
    """Optional: report real invocations for the live connection graph.

    ctx.L is the per-frame lookup (searching/tracking/moving/contacts/…);
    call ctx.hit(subject) for each involved body and ctx.meta(text) for
    the node status line.
    """
    return None


EDGE_SUBJECTS = {
    # labels flowing on an edge while it is active, e.g. UUV ids
    "my-plugin>uuv-control": lambda L: L["moving"],
}


def tick_stages(runtime):
    """Optional: custom plugins insert positioned pipeline stages.

    Return entries {"after": <slot name>, "fn": fn(runtime) -> bool|None};
    fn returning False aborts the tick (pause semantics). Unknown slot
    names append at the end of the pipeline.
    """
    return []


def probe(runtime, params, worker=False):
    """Tool body: runs under runtime.lock. Params arrive as a plain dict —
    validate defensively. Raise MissionError(code, status) for domain
    errors; anything else becomes a generic tool failure. Registration
    review requires execute() to raise on missing required params —
    returning normally is treated as silently accepting invalid input."""
    if not params.get("uuv_id"):
        raise ValueError("uuv_id_required")
    return {"uuv_id": params.get("uuv_id"), "status": "ok"}
