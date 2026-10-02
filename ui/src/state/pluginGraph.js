// Plugin model for the algorithm-logic view.
// Each plugin = one capability of the mission stack. The graph is derived from
// the live mission frame: a plugin lights up while the frame shows UUVs,
// contacts or plans that exercise that capability, and edges carry the
// subjects (UUV ids / contact ids) flowing between them this frame.

export const PLUGIN_DEFS = [
  {
    id: "energy-lifecycle", name: "能源轮换", layer: 0, color: "#0f766e",
    desc: "油量监视、低油量返航、替补艇生成与跟踪交接",
    inputs: "many", outputs: "many",
  },
  {
    id: "task-allocation", name: "任务分配", layer: 1, color: "#1d4ed8",
    desc: "把获批计划的成员 / 责任区指派到具体 UUV",
    inputs: "many", outputs: "many",
  },
  {
    id: "region-partition", name: "动态任务区域划分", layer: 1, color: "#0369a1",
    desc: "把搜索海域划为责任区，随能源与覆盖动态修补",
    inputs: "one", outputs: "many",
  },
  {
    id: "sensor-fusion", name: "多艇方位融合", layer: 2, color: "#0e7490",
    desc: "≥2 艇前视被动方位观测经 EKF 融合为目标估计",
    inputs: "many", outputs: "one",
  },
  {
    id: "path-planning", name: "协同路径规划", layer: 2, color: "#4338ca",
    desc: "虚拟领航点轨迹：引导 UUV 到站位、责任区或出口",
    inputs: "many", outputs: "many",
  },
  {
    id: "coverage-search", name: "区域覆盖搜索", layer: 3, color: "#15803d",
    desc: "侧扫 + 前视主动声纳沿责任区扫线持续覆盖",
    inputs: "one", outputs: "many",
  },
  {
    id: "coop-tracking", name: "协同目标跟踪", layer: 3, color: "#be123c",
    desc: "多艇前视被动声纳在协同区内编队稳定跟踪",
    inputs: "many", outputs: "many",
  },
  {
    id: "reacquire", name: "失联再捕获", layer: 3, color: "#a16207",
    desc: "目标丢失后重搜该区域、再发现并恢复跟踪",
    inputs: "many", outputs: "many",
  },
  {
    id: "uuv-control", name: "UUV 平台控制", layer: 4, color: "#475569",
    desc: "航向 / 速度 / 传感器模式执行与平台遥测",
    inputs: "many", outputs: "one",
  },
];

// Static topology: every edge the algorithm can exercise. `route` maps the
// edge to the frame feature that says it is carrying subjects this frame.
const EDGES = [
  { from: "energy-lifecycle", to: "task-allocation" },
  { from: "energy-lifecycle", to: "path-planning" },
  { from: "energy-lifecycle", to: "region-partition" },
  { from: "region-partition", to: "task-allocation" },
  { from: "task-allocation", to: "coverage-search" },
  { from: "task-allocation", to: "path-planning" },
  { from: "task-allocation", to: "coop-tracking" },
  { from: "task-allocation", to: "reacquire" },
  { from: "path-planning", to: "coverage-search" },
  { from: "path-planning", to: "coop-tracking" },
  { from: "coverage-search", to: "coop-tracking" },
  { from: "sensor-fusion", to: "coop-tracking" },
  { from: "coop-tracking", to: "reacquire" },
  { from: "reacquire", to: "coop-tracking" },
  { from: "coverage-search", to: "uuv-control" },
  { from: "path-planning", to: "uuv-control" },
  { from: "coop-tracking", to: "uuv-control" },
  { from: "reacquire", to: "uuv-control" },
];

export const PLUGIN_EDGES = EDGES;

const EVENT_PLUGIN = {
  tracking_handoff_started: "energy-lifecycle",
  tracking_handoff_completed: "energy-lifecycle",
  debug_fuel_shortage: "energy-lifecycle",
  uuv_exit: "energy-lifecycle",
  uuv_entered: "task-allocation",
  region_repair: "region-partition",
  contact_lost: "reacquire",
  contact_found: "reacquire",
};

function eventSubject(data = {}) {
  return data.uav_id || data.uuv_id || data.observer_id || data.incoming
    || data.departing || data.members?.[0] || data.owner || null;
}

// Derive which plugins are active in this frame, who they are acting on, and
// which edges are carrying subjects.
export function derivePluginGraph(frame, liveEvents = []) {
  const uavs = frame?.uavs || [];
  const contacts = frame?.contacts || [];
  const plans = frame?.plans || [];
  const regions = frame?.search_regions || [];
  const events = (frame?.events || []).concat(liveEvents || []).slice(-12);

  const nodes = {};
  for (const def of PLUGIN_DEFS) {
    nodes[def.id] = { active: false, subjects: [], meta: null };
  }

  const transitToTrack = uavs.filter((u) => u.status === "transit" && u.target_group_id);
  const transitToRegion = uavs.filter((u) => u.status === "transit" && !u.target_group_id);
  const searching = uavs.filter((u) => u.operation_mode === "coverage" && u.status === "searching");
  const tracking = uavs.filter((u) => u.operation_mode === "track");
  const returning = uavs.filter((u) => u.operation_mode === "return" || u.task_phase === "exit" || u.status === "returning");
  const lowFuel = uavs.filter((u) => (u.energy_pct ?? 100) < 20);
  const activePlans = plans.filter((p) => p.status === "active");
  const reacquirePlans = activePlans.filter((p) => p.kind === "reacquire");
  const lostContacts = contacts.filter((c) => c.state === "lost");
  const trackedContacts = contacts.filter((c) => ["tracking", "degraded"].includes(c.state));
  const fusedObservers = [...new Set(trackedContacts.flatMap((c) => c.observers || []))];

  nodes["coverage-search"].active = searching.length > 0;
  nodes["coverage-search"].subjects = searching.map((u) => u.id);
  nodes["coverage-search"].meta = searching.length ? `${searching.length} 艇 · ${regions.filter((r) => r.assigned_uav_id).length} 责任区` : null;

  nodes["path-planning"].active = transitToTrack.length + transitToRegion.length + returning.length > 0;
  nodes["path-planning"].subjects = [...transitToTrack, ...transitToRegion, ...returning].map((u) => u.id);
  nodes["path-planning"].meta = nodes["path-planning"].subjects.length ? `${nodes["path-planning"].subjects.length} 艇在途` : null;

  nodes["coop-tracking"].active = tracking.length > 0 || trackedContacts.length > 0;
  nodes["coop-tracking"].subjects = tracking.map((u) => u.id);
  nodes["coop-tracking"].meta = trackedContacts.length
    ? `${trackedContacts.map((c) => c.contact_id).join("/")} ${trackedContacts[0].state}` : null;

  nodes["sensor-fusion"].active = fusedObservers.length >= 2;
  nodes["sensor-fusion"].subjects = fusedObservers;
  nodes["sensor-fusion"].meta = fusedObservers.length ? `${fusedObservers.length} 源方位融合` : null;

  nodes["reacquire"].active = reacquirePlans.length > 0 || lostContacts.length > 0;
  nodes["reacquire"].subjects = reacquirePlans.flatMap((p) => p.active_members || p.members || []);
  nodes["reacquire"].meta = lostContacts.length ? `${lostContacts.map((c) => c.contact_id).join("/")} 丢失重搜` : reacquirePlans.length ? `${reacquirePlans.length} 个再捕获计划` : null;

  nodes["region-partition"].active = regions.length > 0;
  nodes["region-partition"].subjects = regions.map((r) => r.assigned_uav_id).filter(Boolean);
  nodes["region-partition"].meta = regions.length ? `${regions.length} 责任区` : null;

  nodes["task-allocation"].active = activePlans.length > 0;
  nodes["task-allocation"].subjects = [...new Set(activePlans.flatMap((p) => p.active_members || p.members || []))];
  nodes["task-allocation"].meta = activePlans.length ? `${activePlans.length} 个活跃计划` : null;

  nodes["energy-lifecycle"].active = returning.length > 0 || lowFuel.length > 0;
  nodes["energy-lifecycle"].subjects = [...new Set([...returning, ...lowFuel].map((u) => u.id))];
  nodes["energy-lifecycle"].meta = nodes["energy-lifecycle"].subjects.length
    ? `${nodes["energy-lifecycle"].subjects.length} 艇轮换中` : null;

  const moving = uavs.filter((u) => (u.speed_mps || 0) > 0 || u.status === "searching" || u.status === "transit" || u.operation_mode === "track");
  nodes["uuv-control"].active = moving.length > 0;
  nodes["uuv-control"].subjects = moving.map((u) => u.id);
  nodes["uuv-control"].meta = moving.length ? `${moving.length} 艇受控` : null;

  // Recent events pulse their mapped plugin so transient stages (handoffs,
  // repairs, approvals) stay visible between frames.
  for (const event of events) {
    const pid = EVENT_PLUGIN[event?.type];
    if (!pid || !nodes[pid]) continue;
    nodes[pid].active = true;
    const subject = eventSubject(event.data);
    if (subject && !nodes[pid].subjects.includes(subject)) nodes[pid].subjects.push(subject);
  }

  const edges = EDGES.map(({ from, to }) => {
    const key = `${from}>${to}`;
    let subjects = [];
    if (key === "task-allocation>coverage-search") subjects = searching.map((u) => u.id);
    else if (key === "task-allocation>path-planning") subjects = [...transitToTrack, ...transitToRegion, ...returning].map((u) => u.id);
    else if (key === "task-allocation>coop-tracking") subjects = tracking.map((u) => u.id);
    else if (key === "task-allocation>reacquire") subjects = reacquirePlans.flatMap((p) => p.active_members || p.members || []);
    else if (key === "path-planning>coop-tracking") subjects = transitToTrack.map((u) => u.id);
    else if (key === "path-planning>coverage-search") subjects = transitToRegion.map((u) => u.id);
    else if (key === "energy-lifecycle>path-planning") subjects = returning.map((u) => u.id);
    else if (key === "energy-lifecycle>task-allocation") subjects = lowFuel.map((u) => u.id);
    else if (key === "coverage-search>coop-tracking") subjects = contacts.map((c) => c.contact_id);
    else if (key === "sensor-fusion>coop-tracking") subjects = fusedObservers;
    else if (key === "coop-tracking>reacquire") subjects = lostContacts.map((c) => c.contact_id);
    else if (key === "reacquire>coop-tracking") subjects = trackedContacts.map((c) => c.contact_id);
    else if (key === "region-partition>task-allocation") subjects = regions.map((r) => r.assigned_uav_id).filter(Boolean);
    else if (to === "uuv-control") subjects = moving.map((u) => u.id);
    const active = nodes[from].active && nodes[to].active && (subjects.length > 0 || ["energy-lifecycle>region-partition"].includes(key));
    return { key, from, to, active, subjects };
  });

  return {
    nodes,
    edges,
    sim: {
      simMin: frame?.sim_time_min ?? null,
      frameId: frame?.frame_id ?? null,
      cycle: frame?.cycle ?? null,
      mode: frame?.autonomy_mode ?? null,
    },
  };
}

export const PORT_LABEL = { none: "无", one: "单输入", many: "多输入" };
export const portText = (def) =>
  `输入: ${def.inputs === "none" ? "—" : PORT_LABEL[def.inputs]} · 输出: ${def.outputs === "none" ? "—" : def.outputs === "one" ? "单输出" : "多输出"}`;
