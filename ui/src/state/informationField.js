const DEFAULTS = {
  scanRadiusCells: 2,
  searchHalfLifeMin: 30,
  trackHalfLifeMin: 15,
};

const EMPTY_INFORMATION_FIELD = Object.freeze({
  info_matrix: null,
  value_matrix: null,
  information_version: 0,
  backend_information_version: 0,
  matrix_frame_id: null,
  matrix_sim_time_min: null,
  received_at_ms: null,
  update_count: 0,
  calculation_mode: "frontend-decay",
  scan_radius_cells: DEFAULTS.scanRadiusCells,
  search_half_life_min: DEFAULTS.searchHalfLifeMin,
  track_half_life_min: DEFAULTS.trackHalfLifeMin,
});

function isMatrix(value) {
  return Array.isArray(value) && value.length > 0 && Array.isArray(value[0]);
}

function finiteNumber(value) {
  return Number.isFinite(Number(value)) ? Number(value) : null;
}

function clamp(value, minimum = 0, maximum = 1) {
  return Math.max(minimum, Math.min(maximum, value));
}

function matrixShape(matrix) {
  return {
    cols: isMatrix(matrix) ? matrix.length : 0,
    rows: isMatrix(matrix) ? matrix[0].length : 0,
  };
}

function createMatrix(cols, rows, value) {
  return Array.from({ length: cols }, () => Array(rows).fill(value));
}

function dimensionsForFrame(frame, model) {
  const fromMatrix = matrixShape(frame?.info_matrix);
  if (fromMatrix.cols > 0 && fromMatrix.rows > 0) return fromMatrix;

  const width = Number(frame?.task_area?.width_km);
  const height = Number(frame?.task_area?.height_km);
  const cellSize = Number(frame?.task_area?.cell_size_km);
  if (width > 0 && height > 0 && cellSize > 0) {
    return {
      cols: Math.max(1, Math.round(width / cellSize)),
      rows: Math.max(1, Math.round(height / cellSize)),
    };
  }
  return { cols: model.cols || 30, rows: model.rows || 30 };
}

function resetModel(model, cols, rows) {
  model.cols = cols;
  model.rows = rows;
  model.lastScanTime = createMatrix(cols, rows, -Infinity);
  model.trackScan = createMatrix(cols, rows, false);
  model.previousPositions = new Map();
  model.previousScanKinds = new Map();
  model.infoMatrix = createMatrix(cols, rows, 0);
  model.lastSimTime = 0;
  model.localScanCount = 0;
  model.seededFromBackend = false;
  model.version = 0;
}

export function createInformationFieldModel(options = {}) {
  const model = {
    ...DEFAULTS,
    ...options,
    cols: 0,
    rows: 0,
    lastScanTime: [],
    trackScan: [],
    previousPositions: new Map(),
    previousScanKinds: new Map(),
    infoMatrix: [],
    lastSimTime: 0,
    localScanCount: 0,
    seededFromBackend: false,
    version: 0,
  };
  return model;
}

export function createInformationField() {
  return { ...EMPTY_INFORMATION_FIELD };
}

function getUuvPosition(uav) {
  if (!Array.isArray(uav?.position) || uav.position.length < 2) return null;
  const col = finiteNumber(uav.position[0]);
  const row = finiteNumber(uav.position[1]);
  return col == null || row == null ? null : [col, row];
}

function getScanKind(uav) {
  const taskType = String(uav?.task_visual?.task_type || "").toLowerCase();
  const operationMode = String(uav?.operation_mode || "").toLowerCase();
  const status = String(uav?.status || "").toLowerCase();

  if (
    status === "tracking"
    || operationMode === "track"
    || operationMode === "tracking"
    || taskType === "track"
    || taskType === "tracking"
  ) {
    return "track";
  }
  if (
    status === "searching"
    || operationMode === "coverage"
    || operationMode === "search"
    || taskType === "coverage"
    || taskType === "search"
  ) {
    return "search";
  }
  return null;
}

function markCell(model, col, row, kind, nowMin) {
  if (col < 0 || col >= model.cols || row < 0 || row >= model.rows) return;
  model.lastScanTime[col][row] = nowMin;
  model.trackScan[col][row] = kind === "track";
  model.localScanCount += 1;
}

function markCircle(model, position, kind, nowMin) {
  const radius = Math.max(0, Number(model.scanRadiusCells));
  const minCol = Math.max(0, Math.floor(position[0] - radius - 1));
  const maxCol = Math.min(model.cols - 1, Math.ceil(position[0] + radius + 1));
  const minRow = Math.max(0, Math.floor(position[1] - radius - 1));
  const maxRow = Math.min(model.rows - 1, Math.ceil(position[1] + radius + 1));
  const radiusSquared = radius * radius;

  for (let col = minCol; col <= maxCol; col += 1) {
    for (let row = minRow; row <= maxRow; row += 1) {
      const dx = col - position[0];
      const dy = row - position[1];
      if (dx * dx + dy * dy <= radiusSquared) {
        markCell(model, col, row, kind, nowMin);
      }
    }
  }
}

function markSegment(model, previous, current, kind, nowMin) {
  if (!previous) {
    markCircle(model, current, kind, nowMin);
    return;
  }
  const distance = Math.hypot(current[0] - previous[0], current[1] - previous[1]);
  const samples = Math.max(1, Math.ceil(distance / 0.5));
  for (let index = 0; index <= samples; index += 1) {
    const progress = index / samples;
    markCircle(
      model,
      [
        previous[0] + (current[0] - previous[0]) * progress,
        previous[1] + (current[1] - previous[1]) * progress,
      ],
      kind,
      nowMin,
    );
  }
}

function markBackendFootprint(model, footprint, kind, nowMin) {
  if (!Array.isArray(footprint) || footprint.length === 0) return false;
  for (const point of footprint) {
    if (!Array.isArray(point) || point.length < 2) continue;
    const col = Math.round(Number(point[0]));
    const row = Math.round(Number(point[1]));
    if (Number.isFinite(col) && Number.isFinite(row)) {
      markCell(model, col, row, kind, nowMin);
    }
  }
  return true;
}

function updateScanTimes(model, frame, nowMin) {
  const nextPositions = new Map();
  const nextKinds = new Map();

  for (const uav of frame?.uavs || []) {
    const position = getUuvPosition(uav);
    if (!position) continue;

    const kind = getScanKind(uav);
    if (kind) {
      const usedBackendFootprint = markBackendFootprint(
        model,
        uav.sar_footprint,
        kind,
        nowMin,
      );
      if (!usedBackendFootprint) {
        const previous = model.previousScanKinds.get(uav.id) === kind
          ? model.previousPositions.get(uav.id)
          : null;
        markSegment(model, previous, position, kind, nowMin);
      }
    }

    nextPositions.set(uav.id, position);
    nextKinds.set(uav.id, kind);
  }

  model.previousPositions = nextPositions;
  model.previousScanKinds = nextKinds;
}

function seedFromBackend(model, matrix, nowMin) {
  if (model.seededFromBackend || model.localScanCount > 0 || !isMatrix(matrix)) return;
  const shape = matrixShape(matrix);
  if (shape.cols !== model.cols || shape.rows !== model.rows) return;

  const halfLife = Math.max(1e-6, Number(model.searchHalfLifeMin));
  for (let col = 0; col < model.cols; col += 1) {
    for (let row = 0; row < model.rows; row += 1) {
      const freshness = clamp(Number(matrix[col]?.[row] || 0));
      if (freshness <= 0) continue;
      const age = -Math.log(freshness) * halfLife / Math.log(2);
      model.lastScanTime[col][row] = nowMin - age;
    }
  }
  model.seededFromBackend = true;
}

function recalculateInfo(model, nowMin) {
  const info = createMatrix(model.cols, model.rows, 0);
  for (let col = 0; col < model.cols; col += 1) {
    for (let row = 0; row < model.rows; row += 1) {
      const lastScan = model.lastScanTime[col][row];
      if (!Number.isFinite(lastScan)) continue;
      const age = Math.max(0, nowMin - lastScan);
      const halfLife = model.trackScan[col][row]
        ? Number(model.trackHalfLifeMin)
        : Number(model.searchHalfLifeMin);
      info[col][row] = clamp(Math.exp(-Math.log(2) * age / Math.max(1e-6, halfLife)));
    }
  }
  model.infoMatrix = info;
  return info;
}

/**
 * Recalculate the information quantity from scan timestamps and simulation time.
 *
 * The backend remains the authoritative source when it is running. This local
 * model exists so the extracted UI also owns the complete display-side
 * freshness loop: scan refresh -> last scan time -> half-life decay -> matrix.
 */
export function updateInformationField(previous, incoming, receivedAt = Date.now(), model) {
  if (incoming?.information_source === "backend") {
    return {
      ...createInformationField(),
      info_matrix: incoming.info_matrix ?? previous.info_matrix,
      value_matrix: incoming.value_matrix ?? previous.value_matrix,
      information_version: incoming.information_version ?? incoming.frame_id,
      backend_information_version: incoming.information_version ?? incoming.frame_id,
      received_at_ms: receivedAt,
      calculation_mode: "backend",
    };
  }
  const fieldModel = model || createInformationFieldModel();
  const dimensions = dimensionsForFrame(incoming, fieldModel);
  if (fieldModel.cols !== dimensions.cols || fieldModel.rows !== dimensions.rows) {
    resetModel(fieldModel, dimensions.cols, dimensions.rows);
  }

  const incomingTime = finiteNumber(incoming?.sim_time_min);
  const nowMin = incomingTime == null ? fieldModel.lastSimTime : incomingTime;
  if (nowMin < fieldModel.lastSimTime) {
    resetModel(fieldModel, dimensions.cols, dimensions.rows);
  }

  seedFromBackend(fieldModel, incoming?.info_matrix, nowMin);
  updateScanTimes(fieldModel, incoming, nowMin);
  const infoMatrix = recalculateInfo(fieldModel, nowMin);
  fieldModel.lastSimTime = nowMin;
  fieldModel.version += 1;

  return {
    ...previous,
    info_matrix: infoMatrix,
    value_matrix: isMatrix(incoming?.value_matrix)
      ? incoming.value_matrix
      : previous.value_matrix,
    backend_info_matrix: isMatrix(incoming?.info_matrix)
      ? incoming.info_matrix
      : previous.backend_info_matrix,
    backend_value_matrix: isMatrix(incoming?.value_matrix)
      ? incoming.value_matrix
      : previous.backend_value_matrix,
    information_version: fieldModel.version,
    backend_information_version: finiteNumber(incoming?.information_version)
      ?? previous.backend_information_version,
    matrix_frame_id: incoming?.frame_id ?? previous.matrix_frame_id,
    matrix_sim_time_min: nowMin,
    received_at_ms: receivedAt,
    update_count: previous.update_count + 1,
    calculation_mode: "frontend-decay",
    scan_radius_cells: fieldModel.scanRadiusCells,
    search_half_life_min: fieldModel.searchHalfLifeMin,
    track_half_life_min: fieldModel.trackHalfLifeMin,
  };
}

export function applyInformationField(frame, field) {
  if (!frame) return frame;
  if (frame.information_source === "backend") return frame;
  return {
    ...frame,
    ...(field?.info_matrix ? { info_matrix: field.info_matrix } : {}),
    ...(field?.value_matrix ? { value_matrix: field.value_matrix } : {}),
    __information: {
      version: field?.information_version ?? frame.information_version ?? 0,
      backend_version: field?.backend_information_version ?? frame.information_version ?? 0,
      matrix_frame_id: field?.matrix_frame_id ?? frame.frame_id ?? null,
      matrix_sim_time_min: field?.matrix_sim_time_min ?? frame.sim_time_min ?? null,
      received_at_ms: field?.received_at_ms ?? null,
      update_count: field?.update_count ?? 0,
      calculation_mode: field?.calculation_mode ?? "frontend-decay",
      scan_radius_cells: field?.scan_radius_cells ?? DEFAULTS.scanRadiusCells,
      search_half_life_min: field?.search_half_life_min ?? DEFAULTS.searchHalfLifeMin,
      track_half_life_min: field?.track_half_life_min ?? DEFAULTS.trackHalfLifeMin,
    },
  };
}

export function informationFieldStatus(field) {
  if (!field?.info_matrix) return "waiting";
  if (field.received_at_ms == null) return "ready";
  return "live";
}
