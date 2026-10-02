"""Implementation of the path-planning plugin — internal; the
public interface is re-exported by the package __init__.
"""



def activity(runtime, ctx):
    for uid in ctx.L["transit_track"] + ctx.L["transit_other"] + ctx.L["exiting"]:
        ctx.hit(uid)
    count = len(ctx.L["transit_track"]) + len(ctx.L["transit_other"]) + len(ctx.L["exiting"])
    ctx.meta(f"{count} 艇在途" if count else None)


EDGE_SUBJECTS = {
    "path-planning>coop-tracking": lambda L: L["transit_track"],
    "path-planning>coverage-search": lambda L: L["transit_other"],
    "path-planning>uuv-control": lambda L: L["moving"],
}
