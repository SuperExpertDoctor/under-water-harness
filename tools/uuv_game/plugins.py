"""Plugin registry for the UUV control stack.

Each entry in PLUGIN_SPECS is one control-algorithm capability that the plugin
view renders as a connectable module. `frame_activity` translates the
runtime's authoritative per-UUV dispatch table (runtime.active) plus contacts,
plans and regions into per-frame plugin invocations with subjects, so the
graph shows real invocations with a sim step instead of UI-side inference.
"""

PLUGIN_SPECS = [
    {"id": "energy-lifecycle", "name": "能源轮换", "layer": 0, "color": "#0f766e",
     "desc": "油量监视、低油量返航、替补艇生成与跟踪交接",
     "inputs": "many", "outputs": "many", "core": True},
    {"id": "task-allocation", "name": "任务分配", "layer": 1, "color": "#1d4ed8",
     "desc": "把获批计划的成员 / 责任区指派到具体 UUV",
     "inputs": "many", "outputs": "many", "core": True},
    {"id": "region-partition", "name": "动态任务区域划分", "layer": 1, "color": "#0369a1",
     "desc": "把搜索海域划为责任区，随能源与覆盖动态修补",
     "inputs": "one", "outputs": "many", "core": True},
    {"id": "sensor-fusion", "name": "多艇方位融合", "layer": 2, "color": "#0e7490",
     "desc": "≥2 艇前视被动方位观测经 EKF 融合为目标估计",
     "inputs": "many", "outputs": "one", "core": True},
    {"id": "path-planning", "name": "协同路径规划", "layer": 2, "color": "#4338ca",
     "desc": "虚拟领航点轨迹：引导 UUV 到站位、责任区或出口",
     "inputs": "many", "outputs": "many", "core": True},
    {"id": "coverage-search", "name": "区域覆盖搜索", "layer": 3, "color": "#15803d",
     "desc": "侧扫 + 前视主动声纳沿责任区扫线持续覆盖",
     "inputs": "one", "outputs": "many", "core": True},
    {"id": "coop-tracking", "name": "协同目标跟踪", "layer": 3, "color": "#be123c",
     "desc": "多艇前视被动声纳在协同区内编队稳定跟踪",
     "inputs": "many", "outputs": "many", "core": True},
    {"id": "reacquire", "name": "失联再捕获", "layer": 3, "color": "#a16207",
     "desc": "目标丢失后重搜该区域、再发现并恢复跟踪",
     "inputs": "many", "outputs": "many", "core": True},
    {"id": "uuv-control", "name": "UUV 平台控制", "layer": 4, "color": "#475569",
     "desc": "航向 / 速度 / 传感器模式执行与平台遥测",
     "inputs": "many", "outputs": "one", "core": True},
]

# Static topology: every edge the control stack can exercise.
PLUGIN_EDGES = [
    {"from": "energy-lifecycle", "to": "task-allocation"},
    {"from": "energy-lifecycle", "to": "path-planning"},
    {"from": "energy-lifecycle", "to": "region-partition"},
    {"from": "region-partition", "to": "task-allocation"},
    {"from": "task-allocation", "to": "coverage-search"},
    {"from": "task-allocation", "to": "path-planning"},
    {"from": "task-allocation", "to": "coop-tracking"},
    {"from": "task-allocation", "to": "reacquire"},
    {"from": "path-planning", "to": "coverage-search"},
    {"from": "path-planning", "to": "coop-tracking"},
    {"from": "coverage-search", "to": "coop-tracking"},
    {"from": "sensor-fusion", "to": "coop-tracking"},
    {"from": "coop-tracking", "to": "reacquire"},
    {"from": "reacquire", "to": "coop-tracking"},
    {"from": "coverage-search", "to": "uuv-control"},
    {"from": "path-planning", "to": "uuv-control"},
    {"from": "coop-tracking", "to": "uuv-control"},
    {"from": "reacquire", "to": "uuv-control"},
]


def catalog():
    return {"plugins": PLUGIN_SPECS, "edges": PLUGIN_EDGES}


def frame_activity(runtime):
    """Per-frame plugin invocations derived from the runtime's own state.

    runtime.active is the dispatch table: kind says which plugin owns the boat
    (search/reacquire -> coverage plugins, track -> tracking plugin, exit ->
    energy lifecycle), and phase says where inside that plugin it is
    (transit -> cooperative path planning toward a slot or region).
    """
    nodes = {spec["id"]: {"active": False, "subjects": [], "meta": None} for spec in PLUGIN_SPECS}

    def hit(pid, subject=None):
        node = nodes[pid]
        node["active"] = True
        if subject and subject not in node["subjects"]:
            node["subjects"].append(subject)

    searching, transit_track, transit_other, tracking, exiting, relief = [], [], [], [], [], []
    moving = []
    for uid, action in runtime.active.items():
        kind, phase = action.get("kind"), action.get("phase")
        if kind == "search":
            searching.append(uid)
            hit("coverage-search", uid)
        elif kind == "reacquire":
            searching.append(uid)
            hit("coverage-search", uid)
            hit("reacquire", uid)
        elif kind == "exit":
            exiting.append(uid)
            hit("energy-lifecycle", uid)
            hit("path-planning", uid)
        elif kind == "track":
            if phase == "transit":
                transit_track.append(uid)
                hit("path-planning", uid)
            else:
                tracking.append(uid)
                hit("coop-tracking", uid)
        else:
            transit_other.append(uid)
            hit("path-planning", uid)
        if action.get("relief_member"):
            relief.append(uid)
            hit("energy-lifecycle", uid)
        moving.append(uid)
    low_fuel = [u["id"] for u in runtime.uuvs
                if u["remaining_range_m"] <= 0.2*runtime.config.range_capacity and u["id"] not in exiting]
    for uid in low_fuel:
        hit("energy-lifecycle", uid)

    contacts = [{"contact_id": cid, **c} for cid, c in runtime.contacts.items()]
    tracked = [c for c in contacts if c.get("state") in ("tracking", "degraded")]
    lost = [c for c in contacts if c.get("state") == "lost"]
    observers = sorted({o for c in tracked for o in c.get("observers", [])})
    if len(observers) >= 2:
        nodes["sensor-fusion"]["active"] = True
        nodes["sensor-fusion"]["subjects"] = observers
    if lost:
        nodes["reacquire"]["active"] = True

    plans = [p for p in runtime.plans.values() if p.get("status") == "active"]
    allocated = sorted({m for p in plans for m in p.get("active_members", p.get("members", []))})
    if allocated:
        nodes["task-allocation"]["active"] = True
        nodes["task-allocation"]["subjects"] = allocated
    reacquire_members = sorted({m for p in plans if p.get("kind") == "reacquire"
                                for m in p.get("active_members", p.get("members", []))})
    for uid in reacquire_members:
        hit("reacquire", uid)

    owners = sorted(r["owner"] for r in runtime.regions if r.get("owner"))
    if owners:
        nodes["region-partition"]["active"] = True
        nodes["region-partition"]["subjects"] = owners
    for uid in moving:
        hit("uuv-control", uid)

    nodes["coverage-search"].update(meta=f"{len(searching)} 艇 · {len(owners)} 责任区" if searching else None)
    nodes["path-planning"].update(meta=f"{len(transit_track)+len(transit_other)+len(exiting)} 艇在途"
                                    if transit_track or transit_other or exiting else None)
    nodes["coop-tracking"].update(meta="/".join(c["contact_id"] for c in tracked) + f" {tracked[0]['state']}" if tracked else None)
    nodes["sensor-fusion"].update(meta=f"{len(observers)} 源方位融合" if observers else None)
    nodes["reacquire"].update(meta="/".join(c["contact_id"] for c in lost) + " 丢失重搜" if lost else None)
    nodes["region-partition"].update(meta=f"{len(owners)} 责任区" if owners else None)
    nodes["task-allocation"].update(meta=f"{len(plans)} 个活跃计划" if plans else None)
    nodes["energy-lifecycle"].update(meta=f"{len(set(exiting+relief+low_fuel))} 艇轮换中"
                                     if exiting or relief or low_fuel else None)
    nodes["uuv-control"].update(meta=f"{len(moving)} 艇受控" if moving else None)

    edges = []
    for edge in PLUGIN_EDGES:
        key = f"{edge['from']}>{edge['to']}"
        subjects = []
        if key == "task-allocation>coverage-search":
            subjects = searching
        elif key == "task-allocation>path-planning":
            subjects = transit_track+transit_other+exiting
        elif key == "task-allocation>coop-tracking":
            subjects = tracking
        elif key == "task-allocation>reacquire":
            subjects = reacquire_members
        elif key == "path-planning>coop-tracking":
            subjects = transit_track
        elif key == "path-planning>coverage-search":
            subjects = transit_other
        elif key == "energy-lifecycle>path-planning":
            subjects = exiting
        elif key == "energy-lifecycle>task-allocation":
            subjects = relief+low_fuel
        elif key == "coverage-search>coop-tracking":
            subjects = [c["contact_id"] for c in contacts]
        elif key == "sensor-fusion>coop-tracking":
            subjects = observers
        elif key == "coop-tracking>reacquire":
            subjects = [c["contact_id"] for c in lost]
        elif key == "reacquire>coop-tracking":
            subjects = [c["contact_id"] for c in tracked]
        elif key == "region-partition>task-allocation":
            subjects = owners
        elif edge["to"] == "uuv-control":
            subjects = moving
        active = (nodes[edge["from"]]["active"] and nodes[edge["to"]]["active"]
                  and (subjects or key == "energy-lifecycle>region-partition"))
        edges.append({"key": key, "from": edge["from"], "to": edge["to"],
                      "active": bool(active), "subjects": subjects})

    return {"nodes": nodes, "edges": edges,
            "step": runtime.frame_id, "sim_min": runtime.sim_time/60}
