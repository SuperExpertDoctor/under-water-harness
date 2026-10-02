"""Implementation of the region-partition plugin — internal; the
public interface is re-exported by the package __init__.
"""



def activity(runtime, ctx):
    for uid in ctx.L["owners"]:
        ctx.hit(uid)
    ctx.meta(f"{len(ctx.L['owners'])} 责任区" if ctx.L["owners"] else None)


EDGE_SUBJECTS = {
    "region-partition>task-allocation": lambda L: L["owners"],
}
