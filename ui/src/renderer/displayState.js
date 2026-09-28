const TASK_PHASES = {
  coverage: {
    transit: { label: "转场搜索", tone: "transit", phase: "coverage_transit" },
    transit_astar: { label: "转场搜索", tone: "transit", phase: "coverage_transit" },
    align_scan: { label: "搜索扫描", tone: "search", phase: "coverage_scan" },
    scanning: { label: "搜索扫描", tone: "search", phase: "coverage_scan" },
    completed: { label: "待命", tone: "idle", phase: "idle" },
  },
  probe: {
    near: { label: "近距观察", tone: "observe", phase: "probe_near" },
    closing: { label: "近距观察", tone: "observe", phase: "probe_near" },
    awaiting_assessment: { label: "等待研判", tone: "assessment", phase: "probe_assessment" },
    finished: { label: "待命", tone: "idle", phase: "idle" },
  },
  track: {
    approach_astar: { label: "接近跟踪", tone: "approach", phase: "track_approach" },
    orbit_entry: { label: "接近跟踪", tone: "approach", phase: "track_approach" },
    tracking: { label: "持续跟踪", tone: "track", phase: "track_active" },
    lost: { label: "待命", tone: "idle", phase: "idle" },
    completed: { label: "待命", tone: "idle", phase: "idle" },
  },
  return: {
    return: { label: "返航", tone: "return", phase: "return" },
    transit: { label: "返航", tone: "return", phase: "return" },
  },
  holding: {
    holding: { label: "等待降落", tone: "holding", phase: "holding" },
  },
};

const LEGACY_STATUS = {
  idle: { label: "待命", tone: "idle", phase: "idle" },
  transit: { label: "转场搜索", tone: "transit", phase: "coverage_transit" },
  searching: { label: "搜索扫描", tone: "search", phase: "coverage_scan" },
  tracking: { label: "持续跟踪", tone: "track", phase: "track_active" },
  returning: { label: "返航", tone: "return", phase: "return" },
  holding: { label: "等待降落", tone: "holding", phase: "holding" },
};

const TASK_TYPE_LABELS = {
  idle: "待命",
  coverage: "区域搜索",
  probe: "目标侦察",
  track: "协同跟踪",
  return: "返航",
  holding: "等待降落",
};

const OPERATION_MODE_LABELS = {
  standby: "待命",
  idle: "待命",
  transit: "转场",
  searching: "搜索",
  tracking: "跟踪",
  returning: "返航",
  holding: "等待降落",
  failed: "故障停用",
};

const CONTROL_OWNER_LABELS = {
  system: "系统调度",
  heuristic: "规则控制",
  llm: "智能决策",
  task_allocator: "任务分配器",
  safety: "安全接管",
};

function phaseResult(taskType, phase) {
  return TASK_PHASES[taskType]?.[phase] || null;
}

function probeDisplayState(taskVisual) {
  if (taskVisual.phase === "baseline") {
    return taskVisual.observation_started
      ? { label: "基线观察", tone: "observe", phase: "probe_baseline" }
      : { label: "接近调查", tone: "approach", phase: "probe_approach" };
  }
  return phaseResult("probe", taskVisual.phase)
    || { label: "接近调查", tone: "approach", phase: "probe_approach" };
}

/** Return the one display state shared by map, sidebar, and details. */
export function uavDisplayState(uav = {}) {
  if (uav.operational_status === "failed") {
    return { label: "故障停用", tone: "failed", phase: "failed" };
  }
  if (["exit", "exiting"].includes(uav.task_phase)) return { label: "驶离补换", tone: "return", phase: "exiting" };
  if (uav.operation_mode === "track") {
    if (uav.task_phase === "provisional") return { label: "临时保持接触", tone: "observe", phase: "track_provisional" };
    if (["transit", "tracking_transit"].includes(uav.task_phase)) return { label: "跟踪转场", tone: "approach", phase: "track_approach" };
    if (["acquire", "acquiring"].includes(uav.task_phase)) return { label: "建立协同观测", tone: "observe", phase: "track_acquire" };
    if (uav.effective_tracking === false && uav.task_phase === "tracking") return { label: "观测中断", tone: "assessment", phase: "track_degraded" };
    if (["degraded", "reacquiring"].includes(uav.task_phase)) return { label: "重新搜索", tone: "search", phase: "track_reacquire" };
  }
  const taskVisual = uav.task_visual;
  if (taskVisual && taskVisual.route_source !== "none") {
    if (taskVisual.route_status === "cleared") {
      return { label: "待命", tone: "idle", phase: "idle" };
    }
    if (taskVisual.task_type === "probe") return probeDisplayState(taskVisual);
    const mapped = phaseResult(taskVisual.task_type, taskVisual.phase);
    if (mapped) return mapped;
  }
  return LEGACY_STATUS[uav.status] || LEGACY_STATUS.idle;
}

export function taskDisplayLabel(uav) {
  return uavDisplayState(uav).label;
}

/** Display-only labels. The wire format remains the original uavs[] contract. */
export function taskTypeDisplayLabel(uav = {}) {
  const taskType = uav.task_visual?.task_type || uav.operation_mode || uav.status || "idle";
  return TASK_TYPE_LABELS[taskType] || OPERATION_MODE_LABELS[taskType] || taskType;
}

export function operationModeDisplayLabel(uav = {}) {
  const mode = uav.operation_mode || uav.status || "idle";
  return OPERATION_MODE_LABELS[mode] || mode;
}

export function controlOwnerDisplayLabel(uav = {}) {
  const owner = uav.control_owner || "system";
  return CONTROL_OWNER_LABELS[owner] || owner;
}

export function vehicleDisplayId(id = "") {
  return String(id).replace(/^UAV-/i, "UUV-");
}
