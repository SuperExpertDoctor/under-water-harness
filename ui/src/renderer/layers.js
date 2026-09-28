import { coordToPixel } from "./geometry";
import { markerColor, ownerColor, UAV_STATUS_COLORS } from "./colors";
import { uavDisplayState, vehicleDisplayId } from "./displayState";
import { layoutLabels } from "./labelLayout";

const FONT = '"Fira Code", "Microsoft YaHei", monospace';
const GROUP_COLORS = ["#0891B2", "#D97706", "#65A30D"];
// Offline and historical frames omit the backend's per-boat sensor radius.
const UUV_SCAN_RADIUS_CELLS = 2;

function gridCenter(col, row, cellSize, ox, oy) {
  return { x: ox + (col + 0.5) * cellSize, y: oy + (row + 0.5) * cellSize };
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function normalizeBases(bases, basePosition) {
  const source = bases?.length
    ? bases
    : basePosition
      ? [{ position: basePosition, number: 1 }]
      : [];
  return source;
}

function buildBaseCenters(bases, mapBounds, gridRows = 30) {
  return (bases || []).map((base, index) => {
    const row = Number(base.position?.[1] ?? 10 + index * 10);
    return {
      x: mapBounds.x + mapBounds.width * (0.052 + Math.min(index, 1) * 0.038),
      y: mapBounds.y + mapBounds.height * clamp((row + 0.5) / gridRows, 0.14, 0.86),
    };
  });
}

function baseCenterForUav(uav, baseCenters) {
  if (!baseCenters?.length) return null;
  const number = Number(String(uav.id || "").match(/\d+/)?.[0] || 1);
  return baseCenters[(number - 1) % baseCenters.length];
}

function groundedUavCenter(uav, baseCenters, cellSize) {
  const baseCenter = baseCenterForUav(uav, baseCenters);
  if (!baseCenter) return null;
  const number = Number(String(uav.id || "").match(/\d+/)?.[0] || 1);
  const slot = Math.floor((number - 1) / baseCenters.length) % 5;
  const angle = -Math.PI / 2 + slot * Math.PI * 0.4;
  const radius = Math.max(3, cellSize * 0.34);
  return {
    x: baseCenter.x + Math.cos(angle) * radius,
    y: baseCenter.y + Math.sin(angle) * radius,
  };
}

export function resolveUavDisplayCenter(uav, cellSize, ox, oy, baseCenters) {
  const taskCenter = gridCenter(uav.position[0], uav.position[1], cellSize, ox, oy);
  const baseCenter = groundedUavCenter(uav, baseCenters, cellSize);
  if (uav.status === "idle" || !baseCenter) {
    return baseCenter || taskCenter;
  }

  const progress = Number(uav.transit_progress);
  if (uav.status === "transit" && Number.isFinite(progress)) {
    const departure = clamp(progress, 0, 1);
    return {
      x: baseCenter.x + (taskCenter.x - baseCenter.x) * departure,
      y: baseCenter.y + (taskCenter.y - baseCenter.y) * departure,
    };
  }

  // Historical replay files do not carry transit_progress.  Keep their
  // first assigned frame at the visible base instead of drawing it at the
  // task-grid coordinate that happens to represent the same base location.
  const home = uav.home_base_grid;
  const atHome = home?.length >= 2
    && Math.hypot(Number(uav.position[0]) - Number(home[0]), Number(uav.position[1]) - Number(home[1])) < 1e-4;
  return atHome ? baseCenter : taskCenter;
}

function text(ctx, value, x, y, color = "#0F172A", size = 10, weight = 500) {
  ctx.font = `${weight} ${size}px ${FONT}`;
  ctx.fillStyle = color;
  ctx.fillText(value, x, y);
}

function drawMapImage(ctx, image, bounds) {
  if (!image?.complete || !image.naturalWidth || !image.naturalHeight) return;
  ctx.save();
  ctx.beginPath();
  ctx.rect(bounds.x, bounds.y, bounds.width, bounds.height);
  ctx.clip();
  ctx.globalAlpha = 0.96;
  const scale = Math.max(bounds.width / image.naturalWidth, bounds.height / image.naturalHeight);
  const sourceWidth = bounds.width / scale;
  const sourceHeight = bounds.height / scale;
  ctx.drawImage(image, image.naturalWidth - sourceWidth, (image.naturalHeight - sourceHeight) / 2,
    sourceWidth, sourceHeight, bounds.x, bounds.y, bounds.width, bounds.height);
  ctx.restore();
}

export function drawBackground(
  ctx,
  width,
  height,
  cellSize,
  ox,
  oy,
  mapBounds,
  assets,
  gridCols = 30,
  gridRows = 30,
  taskArea = null,
) {
  const taskWidth = gridCols * cellSize;
  const taskHeight = gridRows * cellSize;
  const widthKm = Number(taskArea?.width_km);
  const heightKm = Number(taskArea?.height_km);
  const areaLabel = Number.isFinite(widthKm) && Number.isFinite(heightKm)
    ? `${Math.round(widthKm)} x ${Math.round(heightKm)} KM`
    : "GRID AREA";
  ctx.fillStyle = "#FFFFFF";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#075AA6";
  ctx.fillRect(mapBounds.x, mapBounds.y, mapBounds.width, mapBounds.height);
  drawMapImage(ctx, assets?.background, mapBounds);
  ctx.strokeStyle = "#0B3857";
  ctx.lineWidth = 2.25;
  ctx.strokeRect(mapBounds.x - 1, mapBounds.y - 1, mapBounds.width + 2, mapBounds.height + 2);
  ctx.save();
  ctx.strokeStyle = "rgba(15, 23, 42, .94)";
  ctx.lineWidth = 1.7;
  ctx.setLineDash([6, 4]);
  ctx.strokeRect(ox - 1, oy - 1, taskWidth + 2, taskHeight + 2);
  ctx.restore();
  ctx.save();
  ctx.fillStyle = "rgba(255, 255, 255, .88)";
  ctx.fillRect(ox + 5, oy + 5, Math.min(taskWidth - 10, Math.max(158, cellSize * 8.7)), Math.max(15, cellSize * 0.7));
  text(ctx, `TASK AREA / ${areaLabel}`, ox + 9, oy + Math.max(16, cellSize * 0.62), "#0B3857", Math.max(7, cellSize * 0.27), 700);
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "#526E7A";
  ctx.font = `600 ${Math.max(7, Math.min(9, cellSize * 0.3))}px ${FONT}`;
  ctx.textAlign = "center";
  const xTicks = Array.from(new Set([0, 5, 10, 15, 20, 25, gridCols - 1]))
    .filter((index) => index >= 0 && index < gridCols);
  const yTicks = Array.from(new Set([0, 5, 10, 15, 20, 25, gridRows - 1]))
    .filter((index) => index >= 0 && index < gridRows);
  for (const index of xTicks) {
    const x = ox + (index + 0.5) * cellSize;
    ctx.fillText(String(index).padStart(2, "0"), x, oy - Math.max(4, cellSize * 0.25));
  }
  ctx.textAlign = "right";
  for (const index of yTicks) {
    const y = oy + (index + 0.5) * cellSize + 3;
    ctx.fillText(String(index).padStart(2, "0"), ox - Math.max(4, cellSize * 0.25), y);
  }
  ctx.restore();
}

export function drawHeatmap(ctx, info, values, cellSize, ox, oy, gridCols = 30, gridRows = 30) {
  drawInformationField(ctx, info, "216, 197, 131", .3, cellSize, ox, oy, gridCols, gridRows);
}

export function drawTargetInformation(ctx, information, cellSize, ox, oy, gridCols = 30, gridRows = 30) {
  drawInformationField(ctx, information, "245, 157, 121", .28, cellSize, ox, oy, gridCols, gridRows);
}

function drawInformationField(ctx, information, color, opacity, cellSize, ox, oy, gridCols, gridRows) {
  for (let col = 0; col < gridCols; col += 1) {
    for (let row = 0; row < gridRows; row += 1) {
      const value = Number(information?.[col]?.[row]);
      if (!Number.isFinite(value) || value <= 0) continue;
      const { x, y } = coordToPixel(col, row, cellSize, ox, oy);
      ctx.fillStyle = `rgba(${color}, ${Math.min(1, value) * opacity})`;
      ctx.fillRect(Math.round(x) + .5, Math.round(y) + .5,
        Math.max(0, Math.round(x + cellSize) - Math.round(x) - 1),
        Math.max(0, Math.round(y + cellSize) - Math.round(y) - 1));
    }
  }
}

export function drawTransparencyOverlay(ctx, info, cellSize, ox, oy, gridCols = 30, gridRows = 30) {
  for (let col = 0; col < gridCols; col += 1) {
    for (let row = 0; row < gridRows; row += 1) {
      const freshness = clamp(Number(info?.[col]?.[row] || 0), 0, 1);
      const { x, y } = coordToPixel(col, row, cellSize, ox, oy);
      ctx.fillStyle = `rgba(15, 23, 42, ${0.1 - freshness * 0.08})`;
      ctx.fillRect(x, y, cellSize, cellSize);
    }
  }
}

export function drawOceanTexture(ctx, cellSize, ox, oy, gridCols = 30, gridRows = 30) {
  const width = gridCols * cellSize;
  const height = gridRows * cellSize;
  ctx.save();
  ctx.beginPath();
  ctx.rect(ox, oy, width, height);
  ctx.clip();
  ctx.strokeStyle = "rgba(8, 145, 178, .16)";
  ctx.lineWidth = 1;
  for (let row = 2; row < gridRows; row += 4) {
    ctx.beginPath();
    for (let col = 0; col <= gridCols; col += 1) {
      const x = ox + col * cellSize;
      const y = oy + (row + Math.sin((col + row) * 0.55) * 0.12) * cellSize;
      if (col === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  ctx.restore();
}

export function drawGridLines(ctx, cellSize, ox, oy, showGrid, gridCols = 30, gridRows = 30) {
  if (!showGrid) return;
  for (let index = 0; index <= Math.max(gridCols, gridRows); index += 1) {
    const major = index % 5 === 0;
    ctx.strokeStyle = major ? "rgba(30, 64, 88, .36)" : "rgba(71, 85, 105, .19)";
    ctx.lineWidth = major ? 0.9 : 0.5;
    ctx.beginPath();
    if (index <= gridCols) {
      ctx.moveTo(ox + index * cellSize, oy);
      ctx.lineTo(ox + index * cellSize, oy + gridRows * cellSize);
    }
    if (index <= gridRows) {
      ctx.moveTo(ox, oy + index * cellSize);
      ctx.lineTo(ox + gridCols * cellSize, oy + index * cellSize);
    }
    ctx.stroke();
  }
}

export function drawObstacles(ctx, obstacles, cellSize, ox, oy, phase) {
  for (const obstacle of obstacles || []) {
    if (obstacle.type === "thunderstorm") {
      const size = Math.max(1, obstacle.size || obstacle.radius * 2 || 1) * cellSize;
      const center = coordToPixel(obstacle.center[0], obstacle.center[1], cellSize, ox, oy);
      const x = center.x - size / 2;
      const y = center.y - size / 2;
      const pulse = 0.56 + Math.sin(phase / 24) * 0.12;
      ctx.fillStyle = "rgba(239, 68, 68, .22)";
      ctx.strokeStyle = `rgba(185, 28, 28, ${pulse + 0.25})`;
      ctx.lineWidth = 1.5;
      ctx.fillRect(x, y, size, size);
      ctx.strokeRect(x, y, size, size);
      ctx.save();
      ctx.strokeStyle = "#FFFFFF";
      ctx.lineWidth = Math.max(1, cellSize * 0.08);
      ctx.beginPath();
      ctx.moveTo(center.x + size * 0.07, center.y - size * 0.28);
      ctx.lineTo(center.x - size * 0.11, center.y - size * 0.02);
      ctx.lineTo(center.x + size * 0.06, center.y - size * 0.02);
      ctx.lineTo(center.x - size * 0.09, center.y + size * 0.29);
      ctx.stroke();
      ctx.restore();
      ctx.save();
      ctx.setLineDash([3, 3]);
      ctx.strokeStyle = "rgba(217, 119, 6, .88)";
      ctx.lineWidth = 1;
      ctx.strokeRect(x - cellSize, y - cellSize, size + 2 * cellSize, size + 2 * cellSize);
      ctx.restore();
      text(ctx, "STORM", x + 3, y + Math.max(10, cellSize * 0.45), "#7F1D1D", Math.max(7, cellSize * 0.28), 700);
    } else {
      const vertices = obstacle.vertices || [];
      if (!vertices.length) continue;
      ctx.beginPath();
      vertices.forEach(([col, row], index) => {
        const point = coordToPixel(col, row, cellSize, ox, oy);
        if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y);
      });
      ctx.closePath();
      ctx.save();
      ctx.globalAlpha = 0.7;
      ctx.fillStyle = "#A16207";
      ctx.strokeStyle = "#713F12";
      ctx.lineWidth = 1.5;
      ctx.fill();
      ctx.stroke();
      ctx.restore();
      const [col, row] = vertices.reduce(
        (sum, vertex) => [sum[0] + vertex[0] / vertices.length, sum[1] + vertex[1] / vertices.length],
        [0, 0],
      );
      const label = obstacle.label || obstacle.id || "ISLAND";
      const point = coordToPixel(col, row, cellSize, ox, oy);
      text(ctx, label, point.x + 3, point.y + 3 + Math.max(7, cellSize * 0.25), "#FFFFFF", Math.max(7, cellSize * 0.25), 700);
    }
  }
}

function taskCells(region) {
  if (Array.isArray(region.cells) && region.cells.length) return region.cells;
  const [c0, r0, c1, r1] = region.bbox;
  const seed = [...String(region.id || "S")].reduce((sum, char) => sum + char.charCodeAt(0), 0);
  const cells = [];
  for (let col = c0; col < c1; col += 1) {
    for (let row = r0; row < r1; row += 1) {
      const edgeDistance = Math.min(col - c0, c1 - 1 - col, row - r0, r1 - 1 - row);
      const carveEdge = edgeDistance === 0 && (col * 13 + row * 7 + seed) % 5 === 0;
      if (!carveEdge) cells.push([col, row]);
    }
  }
  return cells;
}

export function regionLabelCell(region) {
  const cells = taskCells(region);
  if (!cells.length) return null;
  const center = cells.reduce((sum, cell) => [sum[0] + cell[0] / cells.length, sum[1] + cell[1] / cells.length], [0, 0]);
  const distance = (cell) => (cell[0] - center[0]) ** 2 + (cell[1] - center[1]) ** 2;
  // Keep the centroid label inside a real responsibility cell, including concave regions.
  return cells.reduce((closest, cell) => distance(cell) < distance(closest) ? cell : closest);
}

export function drawSearchRegions(ctx, regions, cellSize, ox, oy, selectedId) {
  for (const region of regions || []) {
    const color = ownerColor(region.assigned_uav_id);
    const cells = taskCells(region);
    const occupied = new Set(cells.map(([col, row]) => `${col},${row}`));
    ctx.save();
    const selected = region.assigned_uav_id === selectedId;
    // Responsibility is a boundary, never evidence that the interior was observed.
    ctx.beginPath();
    for (const [col, row] of cells) {
      const { x, y } = coordToPixel(col, row, cellSize, ox, oy);
      for (const [dc, dr, x0, y0, x1, y1] of [
        [-1, 0, x, y, x, y + cellSize], [1, 0, x + cellSize, y, x + cellSize, y + cellSize],
        [0, -1, x, y, x + cellSize, y], [0, 1, x, y + cellSize, x + cellSize, y + cellSize],
      ]) {
        if (occupied.has(`${col + dc},${row + dr}`)) continue;
        ctx.moveTo(x0, y0); ctx.lineTo(x1, y1);
      }
    }
    ctx.strokeStyle = `${color}${selected ? "FF" : "B3"}`;
    ctx.lineWidth = selected ? 2 : 1;
    ctx.setLineDash([]);
    ctx.stroke();
    ctx.restore();
  }
}

export function drawIntents(ctx, intents, statuses, cellSize, ox, oy) {
  const statusById = new Map((statuses || []).map((status) => [status.intent_id, status]));
  for (const intent of intents || []) {
    if (!Array.isArray(intent.bbox) || intent.bbox.length !== 4) continue;
    const [c0, r0, c1, r1] = intent.bbox;
    if (!(c1 > c0 && r1 > r0)) continue;
    const status = statusById.get(intent.intent_id);
    const lifecycle = intent.lifecycle || "active";
    const color = lifecycle === "expired"
      ? "#64748B"
      : lifecycle === "cancelled" ? "#94A3B8" : "#7C3AED";
    const point = coordToPixel(c0, r0, cellSize, ox, oy);
    const width = (c1 - c0) * cellSize;
    const height = (r1 - r0) * cellSize;
    ctx.save();
    ctx.fillStyle = lifecycle === "active" ? "rgba(124, 58, 237, .10)" : "rgba(100, 116, 139, .07)";
    ctx.fillRect(point.x, point.y, width, height);
    ctx.strokeStyle = color;
    ctx.lineWidth = lifecycle === "active" ? 1.8 : 1;
    ctx.setLineDash(lifecycle === "active" ? [5, 3] : [2, 4]);
    ctx.strokeRect(point.x + 1, point.y + 1, Math.max(0, width - 2), Math.max(0, height - 2));
    ctx.restore();
    const coverage = status ? Math.round((status.coverage_ratio || 0) * 100) : null;
    const label = `${intent.intent_id}${coverage == null ? "" : ` ${coverage}%`}`;
    text(ctx, label, point.x + 4, point.y + Math.max(11, cellSize * 0.48), color, Math.max(7, cellSize * 0.27), 700);
  }
}

export function drawTrackRegions(ctx, regions, contacts, cellSize, ox, oy) {
  for (const region of regions || []) {
    const [c0, r0, c1, r1] = region.bbox;
    const { x, y } = coordToPixel(c0, r0, cellSize, ox, oy);
    ctx.fillStyle = "rgba(190, 18, 60, .06)";
    ctx.fillRect(x, y, (c1 - c0) * cellSize, (r1 - r0) * cellSize);
    ctx.strokeStyle = "#BE123C";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 4]);
    ctx.strokeRect(x, y, (c1 - c0) * cellSize, (r1 - r0) * cellSize);
    ctx.setLineDash([]);
    const group = (contacts || []).filter((contact) => {
      const id = contact.contact_id || contact.group_id;
      return id === region.target_group_id && contact.state !== "departed";
    });
    if (group.length) {
      const centerCol = group.reduce((sum, contact) => sum + (contact.estimated_position || contact.position)[0], 0) / group.length;
      const centerRow = group.reduce((sum, contact) => sum + (contact.estimated_position || contact.position)[1], 0) / group.length;
      const center = gridCenter(centerCol, centerRow, cellSize, ox, oy);
      ctx.strokeStyle = "rgba(190, 18, 60, .72)";
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.arc(center.x, center.y, 1.8 * cellSize, 0, Math.PI * 2);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
}

function contactColor(contact) {
  if (contact?.state === "lost" || contact?.state === "departed") return "#64748B";
  if (contact?.vessel_class === "type_ii") return "#BE123C";
  if (contact?.vessel_class === "type_i") return "#0F766E";
  return "#B45309";
}

function drawContactVessel(ctx, center, size, color, heading, selected) {
  ctx.save();
  ctx.translate(center.x, center.y);
  if (heading != null) ctx.rotate(heading);
  ctx.fillStyle = color;
  ctx.strokeStyle = "#FFFFFF";
  ctx.lineWidth = selected ? 1.2 : 0.8;
  ctx.beginPath();
  if (heading == null) {
    ctx.moveTo(0, -size * 1.08);
    ctx.lineTo(size * 0.72, 0);
    ctx.lineTo(0, size * 1.08);
    ctx.lineTo(-size * 0.72, 0);
  } else {
    ctx.moveTo(size * 1.25, 0);
    ctx.lineTo(-size * 0.62, -size * 0.72);
    ctx.lineTo(-size * 0.36, 0);
    ctx.lineTo(-size * 0.62, size * 0.72);
  }
  ctx.closePath();
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

export function drawContacts(ctx, contacts, cellSize, ox, oy, selectedId, phase = 0, assets) {
  for (const contact of contacts || []) {
    const position = contact.estimated_position;
    if (!Array.isArray(position) || position.length < 2) continue;
    const color = contactColor(contact);
    const center = gridCenter(Number(position[0]), Number(position[1]), cellSize, ox, oy);
    const radius = Math.max(4, cellSize * (contact.contact_id === selectedId ? 0.34 : 0.25));
    const samples = (contact.samples || []).filter((sample) => Array.isArray(sample.position));
    if (samples.length > 1) {
      ctx.save();
      ctx.strokeStyle = `${color}66`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      samples.forEach((sample, index) => {
        const point = gridCenter(sample.position[0], sample.position[1], cellSize, ox, oy);
        if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y);
      });
      ctx.stroke();
      ctx.restore();
    }
    const aisSample = [...samples].reverse().find((sample) => sample.source === "ais");
    if (aisSample && contact.state !== "departed") {
      const aisPoint = gridCenter(aisSample.position[0], aisSample.position[1], cellSize, ox, oy);
      ctx.save();
      ctx.strokeStyle = "rgba(100, 116, 139, .62)";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(center.x, center.y);
      ctx.lineTo(aisPoint.x, aisPoint.y);
      ctx.stroke();
      ctx.restore();
    }
    ctx.save();
    ctx.globalAlpha = contact.state === "lost" ? 0.5 : 1;
    ctx.fillStyle = `${color}22`;
    ctx.strokeStyle = color;
    ctx.lineWidth = contact.contact_id === selectedId ? 2 : 1.2;
    ctx.beginPath();
    ctx.arc(center.x, center.y, radius + 3, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    if (contact.contact_id === selectedId) {
      ctx.globalAlpha = 0.78;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.arc(center.x, center.y, radius + 7 + Math.sin(phase / 10) * 1.5, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.fillStyle = color;
    const velocity = contact.estimated_velocity;
    const speed = Array.isArray(velocity) && velocity.length >= 2
      ? Math.hypot(Number(velocity[0]), Number(velocity[1]))
      : 0;
    const heading = speed > 1e-3
      ? Math.atan2(Number(velocity[1]), Number(velocity[0]))
      : null;
    const model = assets?.submarine;
    // Contact velocity is already in screen-grid coordinates, unlike UUV world heading.
    const rendered = Number.isFinite(heading) && drawSprite(ctx, model,
      { x: 0, y: 0, width: model?.naturalWidth, height: model?.naturalHeight }, center,
      vesselLength(cellSize, contact.contact_id === selectedId, true), Math.PI + heading);
    if (!rendered) drawContactVessel(
      ctx,
      center,
      radius,
      color,
      Number.isFinite(heading) ? heading : null,
      contact.contact_id === selectedId,
    );
    ctx.restore();
  }
}

export function drawPassiveEvidence(ctx, evidence, cellSize, ox, oy, phase = 0) {
  for (const item of evidence || []) {
    if (item.kind === "passive_bearing") {
      const origin = item.observer_position || item.origin;
      if (!Array.isArray(origin) || origin.length < 2) continue;
      const center = gridCenter(Number(origin[0]), Number(origin[1]), cellSize, ox, oy);
      // The public evidence contract is bearing-only. Use the configured
      // receiver envelope as a rendering bound instead of a measured range.
      const radius = Math.max(cellSize * 1.5, 10 * cellSize);
      const bearing = Number(item.bearing_deg || 0) * Math.PI / 180;
      const spread = Math.max(0.04, Number(item.bearing_std_deg || 3) * 2 * Math.PI / 180);
      ctx.save();
      ctx.fillStyle = "rgba(14, 116, 144, .09)";
      ctx.strokeStyle = "rgba(14, 116, 144, .64)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(center.x, center.y);
      ctx.arc(center.x, center.y, radius, bearing - spread, bearing + spread);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#0E7490";
      ctx.beginPath();
      ctx.arc(center.x, center.y, Math.max(2, cellSize * 0.1), 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    } else if (item.kind === "passive_position") {
      const position = item.position;
      if (!Array.isArray(position) || position.length < 2) continue;
      const center = gridCenter(Number(position[0]), Number(position[1]), cellSize, ox, oy);
      const size = Math.max(4, cellSize * 0.25);
      const pulse = 1 + 0.12 * Math.sin(phase / 8);
      ctx.save();
      ctx.translate(center.x, center.y);
      ctx.rotate(Math.PI / 4);
      ctx.fillStyle = "rgba(5, 150, 105, .18)";
      ctx.strokeStyle = "#047857";
      ctx.lineWidth = 1.5;
      ctx.fillRect(-size * pulse, -size * pulse, size * 2 * pulse, size * 2 * pulse);
      ctx.strokeRect(-size * pulse, -size * pulse, size * 2 * pulse, size * 2 * pulse);
      ctx.restore();
    }
  }
}

export function drawScenarioVessels(ctx, vessels, cellSize, ox, oy, selectedId) {
  for (const vessel of vessels || []) {
    if (!Array.isArray(vessel.position) || vessel.position.length < 2) continue;
    const center = gridCenter(Number(vessel.position[0]), Number(vessel.position[1]), cellSize, ox, oy);
    const selected = vessel.scenario_entity_id === selectedId;
    const color = vessel.vessel_class === "type_ii" ? "#B45309" : "#0369A1";
    const radius = Math.max(4, cellSize * (selected ? 0.34 : 0.27));
    ctx.save();
    ctx.fillStyle = `${color}20`;
    ctx.strokeStyle = color;
    ctx.lineWidth = selected ? 2 : 1.2;
    ctx.beginPath();
    ctx.arc(center.x, center.y, radius + (selected ? 5 : 3), 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(center.x, center.y - radius);
    ctx.lineTo(center.x + radius, center.y + radius);
    ctx.lineTo(center.x - radius, center.y + radius);
    ctx.closePath();
    ctx.fill();
    ctx.restore();
  }
}

export function drawPaths(ctx, uavs, cellSize, ox, oy, selectedId, baseCenters) {
  for (const uav of uavs || []) {
    const mission = uav.mission_route || uav.planned_path || [];
    const planned = uav.planned_path || [];
    const baseGrid = uav.home_base_grid;
    const isSelected = uav.id === selectedId;

    // Departing UAV: the visual base star lives on the mainland (map
    // coords) while planned_path[0] lives on the ocean grid.  Draw a
    // departure leg bridging the two coordinate spaces so the route
    // reads as originating from the visible red-star marker.
    const departBase = baseCenters?.length
      ? baseCenters[(Number(String(uav.id || "").match(/\d+/)?.[0] || 1) - 1) % baseCenters.length]
      : null;
    const isDeparting = (
      departBase
      && planned.length >= 1
      && uav.status !== "idle"
      && uav.status !== "returning"
      && uav.status !== "holding"
    );
    // Avoid drawing the departure leg when the UAV has already
    // travelled well beyond the first few waypoints (the connector
    // would then bisect the screen).
    let showDepartLeg = false;
    let departGridPt = null;
    if (isDeparting && planned.length > 0) {
      const firstPose = planned[0];
      departGridPt = gridCenter(firstPose[0], firstPose[1], cellSize, ox, oy);
      // The departure leg is meaningful when the UAV's home base
      // grid is close to planned_path[0] (i.e. a fresh sortie, not
      // a mid-mission replan).
      if (baseGrid && baseGrid.length >= 2) {
        const homePt = gridCenter(baseGrid[0], baseGrid[1], cellSize, ox, oy);
        showDepartLeg = Math.hypot(homePt.x - departGridPt.x, homePt.y - departGridPt.y) < cellSize * 4;
      }
    }

    // ── Full mission route (dashed, dim) ──────────────────────────
    if (isSelected && mission.length >= 2 && uav.status !== "idle") {
      ctx.save();
      ctx.strokeStyle = `${ownerColor(uav.id)}${isSelected ? "B0" : "55"}`;
      ctx.lineWidth = isSelected ? 1.4 : 0.9;
      ctx.setLineDash([4, 5]);
      ctx.beginPath();
      // Prepend departure leg from the mainland base star
      if (showDepartLeg && departGridPt) {
        ctx.moveTo(departBase.x, departBase.y);
        ctx.lineTo(departGridPt.x, departGridPt.y);
      }
      mission.forEach((pose, index) => {
        const pt = gridCenter(pose[0], pose[1], cellSize, ox, oy);
        if (index === 0 && showDepartLeg) {
          // Already connected from base star — skip duplicate moveTo
        } else if (index === 0) {
          ctx.moveTo(pt.x, pt.y);
        } else {
          ctx.lineTo(pt.x, pt.y);
        }
      });
      // Returning / holding: draw the final leg back to base
      if (baseGrid && baseGrid.length >= 2
          && (uav.status === "returning" || uav.status === "holding")) {
        const basePt = gridCenter(baseGrid[0], baseGrid[1], cellSize, ox, oy);
        ctx.lineTo(basePt.x, basePt.y);
      }
      ctx.stroke();
      ctx.restore();
    }

    // ── Remaining planned path (solid, prominent) ─────────────────
    if (planned.length >= 2 && uav.status !== "idle") {
      ctx.save();
      ctx.strokeStyle = `${ownerColor(uav.id)}${isSelected ? "FF" : "C0"}`;
      ctx.lineWidth = isSelected ? 2.2 : 1.3;
      ctx.setLineDash([]);
      ctx.beginPath();
      if (showDepartLeg && departGridPt) {
        ctx.moveTo(departBase.x, departBase.y);
        ctx.lineTo(departGridPt.x, departGridPt.y);
      }
      (isSelected ? planned : planned.slice(0, 18)).forEach((pose, index) => {
        const pt = gridCenter(pose[0], pose[1], cellSize, ox, oy);
        if (index === 0 && showDepartLeg) {
          // connected from base star
        } else if (index === 0) {
          ctx.moveTo(pt.x, pt.y);
        } else {
          ctx.lineTo(pt.x, pt.y);
        }
      });
      ctx.stroke();
      ctx.restore();
    }

    // ── Standoff orbit ring (tracking) ────────────────────────────
    if (uav.status === "tracking" && uav.target_group_id) {
      ctx.save();
      ctx.strokeStyle = isSelected ? "rgba(190, 18, 60, .72)" : "rgba(190, 18, 60, .34)";
      ctx.lineWidth = isSelected ? 1.4 : 0.8;
      ctx.setLineDash([3, 4]);
      const lastPt = planned.length
        ? gridCenter(planned[planned.length - 1][0], planned[planned.length - 1][1], cellSize, ox, oy)
        : uav.position?.length >= 2
          ? gridCenter(uav.position[0], uav.position[1], cellSize, ox, oy)
          : null;
      if (lastPt) {
        ctx.beginPath();
        ctx.arc(lastPt.x, lastPt.y, 1.8 * cellSize, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.restore();
    }

    // ── Storm-avoidance detour path (cyan, prominent) ────────────
    const avoidancePath = uav.avoidance_path || [];
    if (avoidancePath.length >= 2) {
      ctx.save();
      ctx.strokeStyle = "rgba(8, 145, 178, .92)";
      ctx.lineWidth = 1.6;
      ctx.setLineDash([5, 3]);
      ctx.beginPath();
      avoidancePath.forEach((pose, index) => {
        const pt = gridCenter(pose[0], pose[1], cellSize, ox, oy);
        if (index === 0) ctx.moveTo(pt.x, pt.y);
        else ctx.lineTo(pt.x, pt.y);
      });
      ctx.stroke();
      ctx.restore();
    }
  }
}

export function drawUavTrails(ctx, uavs, cellSize, ox, oy, selectedId, trailMode) {
  for (const uav of uavs || []) {
    const trail = uav.trail || [];
    if (trail.length < 2) continue;
    const color = ownerColor(uav.id);
    ctx.save();
    ctx.lineCap = "round";

    // ── Mode: full ── uniform thin line over the entire trail ─────
    if (trailMode === "full") {
      ctx.strokeStyle = color;
      ctx.globalAlpha = uav.id === selectedId ? 0.9 : 0.62;
      ctx.lineWidth = uav.id === selectedId ? 2.2 : 1.35;
      ctx.beginPath();
      trail.forEach(([col, row], index) => {
        const point = gridCenter(col, row, cellSize, ox, oy);
        if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y);
      });
      ctx.stroke();
      ctx.restore();
      continue;
    }

    // ── Mode: comet ── filled tapered shape, wide at UAV, point at tail
    if (trailMode === "comet") {
      const maxHalfWidth = cellSize * (uav.id === selectedId ? 0.52 : 0.30);
      const maxAlpha = uav.id === selectedId ? 0.48 : 0.26;
      // Build polygon vertices from tail to head along left edge,
      // then back along right edge.
      const left = [];
      const right = [];
      for (let i = 0; i < trail.length; i += 1) {
        const t = i / Math.max(1, trail.length - 1); // 0→tail  1→head
        const halfW = Math.max(0.2, maxHalfWidth * t * t); // quadratic taper
        let dx = 0, dy = 0;
        if (i < trail.length - 1) {
          dx = trail[i + 1][0] - trail[i][0];
          dy = trail[i + 1][1] - trail[i][1];
        } else if (i > 0) {
          dx = trail[i][0] - trail[i - 1][0];
          dy = trail[i][1] - trail[i - 1][1];
        }
        const len = Math.hypot(dx, dy) || 1;
        const px = -dy / len * halfW;
        const py = dx / len * halfW;
        const pt = gridCenter(trail[i][0], trail[i][1], cellSize, ox, oy);
        left.push({ x: pt.x + px, y: pt.y + py, t });
        right.push({ x: pt.x - px, y: pt.y - py, t });
      }
      // Draw filled polygon
      const headAlpha = maxAlpha;
      ctx.globalAlpha = headAlpha;
      ctx.fillStyle = color;
      ctx.beginPath();
      for (let i = 0; i < left.length; i += 1) {
        if (i === 0) ctx.moveTo(left[i].x, left[i].y);
        else ctx.lineTo(left[i].x, left[i].y);
      }
      for (let i = right.length - 1; i >= 0; i -= 1) {
        ctx.lineTo(right[i].x, right[i].y);
      }
      ctx.closePath();
      ctx.fill();
      // Thin centerline on top for definition
      ctx.globalAlpha = headAlpha * 1.3;
      ctx.strokeStyle = color;
      ctx.lineWidth = Math.max(0.5, cellSize * (uav.id === selectedId ? 0.12 : 0.07));
      ctx.beginPath();
      trail.forEach(([col, row], index) => {
        const pt = gridCenter(col, row, cellSize, ox, oy);
        if (index === 0) ctx.moveTo(pt.x, pt.y);
        else ctx.lineTo(pt.x, pt.y);
      });
      ctx.stroke();
      // Glow head dot
      if (trail.length) {
        const head = trail[trail.length - 1];
        const h = gridCenter(head[0], head[1], cellSize, ox, oy);
        ctx.globalAlpha = headAlpha * 1.5;
        ctx.fillStyle = "#FFFFFF";
        ctx.beginPath();
        ctx.arc(h.x, h.y, maxHalfWidth * 0.85, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.restore();
      continue;
    }

    // ── Mode: tail (default) ── gradient-width line, last 72 points
    const start = Math.max(1, trail.length - 72);
    for (let index = start; index < trail.length; index += 1) {
      const previous = gridCenter(trail[index - 1][0], trail[index - 1][1], cellSize, ox, oy);
      const current = gridCenter(trail[index][0], trail[index][1], cellSize, ox, oy);
      const progress = (index - start + 1) / Math.max(1, trail.length - start);
      ctx.strokeStyle = color;
      ctx.globalAlpha = 0.1 + progress * (uav.id === selectedId ? 0.82 : 0.52);
      ctx.lineWidth = (uav.id === selectedId ? 2.25 : 1.45) * (0.62 + progress * 0.38);
      ctx.beginPath();
      ctx.moveTo(previous.x, previous.y);
      ctx.lineTo(current.x, current.y);
      ctx.stroke();
    }
    ctx.restore();
  }
}

export function drawUavScanRanges(ctx, uavs, cellSize, ox, oy, baseCenters, selectedId) {
  for (const uav of uavs || []) {
    if (["idle", "failed"].includes(uav.status)) continue;
    if (uav.sensor_mode && uav.sensor_mode !== "active") continue;
    const radius = Math.max(0, uav.sensor_radius_cells ?? UUV_SCAN_RADIUS_CELLS) * cellSize;
    const center = resolveUavDisplayCenter(uav, cellSize, ox, oy, baseCenters);
    if (!center) continue;
    const selected = uav.id === selectedId;
    ctx.save();
    ctx.beginPath();
    ctx.arc(center.x, center.y, radius, 0, Math.PI * 2);
    ctx.fillStyle = selected ? "rgba(14, 165, 233, .16)" : "rgba(14, 165, 233, .09)";
    ctx.strokeStyle = selected ? "rgba(125, 211, 252, .98)" : "rgba(56, 189, 248, .72)";
    ctx.lineWidth = selected ? 2 : 1.2;
    ctx.setLineDash(selected ? [] : [5, 4]);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }
}

export function drawMarkers(ctx, markers, cellSize, ox, oy, time, phase) {
  for (const marker of markers || []) {
    const age = time - marker.created_time_min;
    if (age > 60) continue;
    const center = gridCenter(marker.position[0], marker.position[1], cellSize, ox, oy);
    const color = markerColor(age);
    ctx.save();
    ctx.globalAlpha = color.alpha;
    ctx.fillStyle = color.fill;
    ctx.beginPath();
    ctx.arc(center.x, center.y, 4 + Math.sin(phase / 12) * 1.5, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    text(ctx, marker.id, center.x + 7, center.y - 7, "#0F172A", 10);
  }
}

function drawGroupRings(ctx, ships, cellSize, ox, oy) {
  const groups = new Map();
  for (const ship of ships || []) {
    if (ship.departed) continue;
    const members = groups.get(ship.group_id) || [];
    members.push(ship);
    groups.set(ship.group_id, members);
  }
  for (const [groupId, members] of groups) {
    if (!members.length) continue;
    const col = members.reduce((sum, ship) => sum + ship.position[0], 0) / members.length;
    const row = members.reduce((sum, ship) => sum + ship.position[1], 0) / members.length;
    const center = gridCenter(col, row, cellSize, ox, oy);
    ctx.save();
    ctx.setLineDash([2, 3]);
    const groupIndex = Math.max(0, Number(String(groupId).replace(/\D/g, "")) - 1) % GROUP_COLORS.length;
    const color = GROUP_COLORS[groupIndex];
    ctx.strokeStyle = `${color}90`;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(center.x, center.y, Math.max(9, cellSize * 1.22), 0, Math.PI * 2);
    ctx.stroke();
    ctx.restore();
    text(ctx, groupId, center.x + 5, center.y - Math.max(9, cellSize * 1.28), color, Math.max(7, cellSize * 0.25), 700);
  }
}

function vesselLength(cellSize, selected, target = false) {
  return Math.max(target ? 34 : 22, Math.min(target ? 52 : 36, cellSize * (target ? 2.8 : 1.9))) * (selected ? 1.15 : 1);
}

function drawSprite(ctx, image, source, center, width, rotation) {
  if (!image?.complete || !image.naturalWidth || !image.naturalHeight) return false;
  const height = width * source.height / source.width;
  ctx.save();
  ctx.translate(center.x, center.y);
  ctx.rotate(rotation);
  ctx.drawImage(
    image,
    source.x,
    source.y,
    source.width,
    source.height,
    -width / 2,
    -height / 2,
    width,
    height,
  );
  ctx.restore();
  return true;
}

function drawShipHull(ctx, ship, center, size, color, assets) {
  const carrier = ship.ship_type === "carrier";
  const model = carrier ? assets?.carrier : assets?.destroyer;
  const source = carrier
    ? { x: 32, y: 74, width: 1472, height: 842 }
    : { x: 400, y: 78, width: 224, height: 1290 };
  const modelWidth = carrier
    ? Math.max(22, cellSizeForShip(size, true))
    : Math.max(6.5, size * 0.82);
  if (drawSprite(
    ctx,
    model,
    source,
    center,
    modelWidth,
    // The carrier image has its bow to the left; the destroyer image has
    // its bow at the top. Canvas heading zero points to the right.
    (Number(ship.heading_deg) || 0) * Math.PI / 180 + (carrier ? Math.PI : Math.PI / 2),
  )) return;
  ctx.fillStyle = color;
  if (carrier) {
    ctx.fillRect(center.x - size * 1.15, center.y - size * 0.52, size * 2.3, size * 1.04);
    ctx.fillStyle = "#FFFFFF";
    ctx.fillRect(center.x - size * 0.15, center.y - size * 0.42, size * 0.34, size * 0.84);
    ctx.strokeStyle = "#334155";
    ctx.lineWidth = 0.8;
    ctx.strokeRect(center.x - size * 1.15, center.y - size * 0.52, size * 2.3, size * 1.04);
    return;
  }
  ctx.beginPath();
  ctx.moveTo(center.x + size, center.y);
  ctx.lineTo(center.x - size * 0.7, center.y - size * 0.58);
  ctx.lineTo(center.x - size * 0.34, center.y);
  ctx.lineTo(center.x - size * 0.7, center.y + size * 0.58);
  ctx.closePath();
  ctx.fill();
}

function cellSizeForShip(size, carrier) {
  return size * (carrier ? 4.6 : 4.15);
}

function drawClassificationSymbol(ctx, ship, center, size, classification) {
  if (ship.departed) return;
  ctx.save();
  ctx.lineWidth = 1;
  if (classification === "type_ii") {
    const x = center.x + size + 5;
    const y = center.y - size - 1;
    ctx.strokeStyle = "#F87171";
    ctx.beginPath();
    ctx.arc(x, y - 2, 1.3, 0, Math.PI * 2);
    ctx.moveTo(x, y - 0.5);
    ctx.lineTo(x, y + 5);
    ctx.moveTo(x - 4, y + 2);
    ctx.lineTo(x + 4, y + 2);
    ctx.moveTo(x - 4, y + 2);
    ctx.quadraticCurveTo(x - 2, y + 6, x, y + 6);
    ctx.quadraticCurveTo(x + 2, y + 6, x + 4, y + 2);
    ctx.stroke();
  } else if (classification === "type_i") {
    ctx.fillStyle = "#0369A1";
    ctx.beginPath();
    ctx.moveTo(center.x + size + 2, center.y - 2);
    ctx.lineTo(center.x + size + 9, center.y - 2);
    ctx.lineTo(center.x + size + 6, center.y + 4);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
}

export function drawShips(ctx, ships, cellSize, ox, oy, assets, gridCols = 30, gridRows = 30) {
  const observedShips = (ships || []).filter((ship) => ship?.is_detected);
  drawGroupRings(ctx, observedShips, cellSize, ox, oy);
  for (const ship of observedShips) {
    const classification = ship.vessel_class || ship.assessment?.vessel_class || "unknown";
    const color = classification === "type_ii"
      ? "#E11D48"
      : classification === "type_i" ? "#0369A1" : "#CA8A04";
    const size = Math.max(4, cellSize * (ship.ship_type === "carrier" ? 0.38 : 0.28));
    if (ship.trail?.length > 1) {
      ctx.save();
      ctx.globalAlpha = ship.departed ? 0.22 : 1;
      ctx.strokeStyle = `${color}55`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ship.trail.forEach(([col, row], index) => {
        const point = gridCenter(col, row, cellSize, ox, oy);
        if (index === 0) ctx.moveTo(point.x, point.y); else ctx.lineTo(point.x, point.y);
      });
      ctx.stroke();
      ctx.restore();
    }
    const rawCenter = gridCenter(ship.position[0], ship.position[1], cellSize, ox, oy);
    const center = {
      x: clamp(rawCenter.x, ox + size + 2, ox + gridCols * cellSize - size - 2),
      y: clamp(rawCenter.y, oy + size + 2, oy + gridRows * cellSize - size - 2),
    };
    ctx.save();
    ctx.globalAlpha = ship.departed ? 0.36 : 1;
    drawShipHull(ctx, ship, center, size, color, assets);
    if (ship.is_detected && !ship.departed) {
      ctx.strokeStyle = color;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(center.x, center.y, size + 3, 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
    drawClassificationSymbol(ctx, ship, center, size, classification);
    if (ship.ais?.reported_position && !ship.departed) {
      const report = gridCenter(ship.ais.reported_position[0], ship.ais.reported_position[1], cellSize, ox, oy);
      ctx.save();
      ctx.strokeStyle = classification === "type_ii"
        ? "rgba(251, 113, 133, .72)" : "rgba(148, 163, 184, .52)";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(center.x, center.y);
      ctx.lineTo(report.x, report.y);
      ctx.stroke();
      ctx.restore();
    }
  }
}

function drawBaseStar(ctx, center, outerRadius, innerRadius) {
  ctx.beginPath();
  for (let point = 0; point < 10; point += 1) {
    const radius = point % 2 === 0 ? outerRadius : innerRadius;
    const angle = -Math.PI / 2 + point * Math.PI / 5;
    const x = center.x + Math.cos(angle) * radius;
    const y = center.y + Math.sin(angle) * radius;
    if (point === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.closePath();
}

export function drawBases(ctx, bases, baseCenters, cellSize, phase) {
  for (const [index, base] of (bases || []).entries()) {
    const center = baseCenters[index];
    if (!center) continue;
    const color = "#DC2626";
    const outerRadius = Math.max(8, cellSize * 0.48);
    const innerRadius = outerRadius * 0.46;
    ctx.save();
    drawBaseStar(ctx, center, outerRadius, innerRadius);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#7F1D1D";
    ctx.lineWidth = 1.3;
    ctx.stroke();
    ctx.restore();
  }
}

export function drawUavs(ctx, uavs, cellSize, ox, oy, selectedId, assets, baseCenters) {
  for (const uav of uavs || []) {
    const center = resolveUavDisplayCenter(uav, cellSize, ox, oy, baseCenters);
    const color = UAV_STATUS_COLORS[uav.status] || "#94A3B8";
    const size = Math.max(5, cellSize * (uav.id === selectedId ? 0.42 : 0.32));
    ctx.save();
    const renderedModel = drawSprite(
      ctx,
      assets?.uav,
      { x: 0, y: 0, width: assets?.uav?.naturalWidth, height: assets?.uav?.naturalHeight },
      center,
      vesselLength(cellSize, uav.id === selectedId),
      Math.PI - (Number.isFinite(uav.heading_deg) ? uav.heading_deg : 0) * Math.PI / 180,
    );
    if (!renderedModel) {
      ctx.translate(center.x, center.y);
      ctx.rotate(-(uav.heading_deg || 0) * Math.PI / 180);
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(size * 1.25, 0);
      ctx.lineTo(-size * 0.3, -size * 0.22);
      ctx.lineTo(-size * 0.85, -size);
      ctx.lineTo(-size * 0.55, -size * 0.12);
      ctx.lineTo(-size, 0);
      ctx.lineTo(-size * 0.55, size * 0.12);
      ctx.lineTo(-size * 0.85, size);
      ctx.lineTo(-size * 0.3, size * 0.22);
      ctx.closePath();
      ctx.fill();
    }
    ctx.restore();
    if (renderedModel) {
      ctx.save();
      ctx.strokeStyle = ownerColor(uav.id);
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(center.x, center.y, vesselLength(cellSize, uav.id === selectedId) / 2 + 2, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
    }
    if (uav.id === selectedId) {
      ctx.strokeStyle = "#0F172A";
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(center.x, center.y, size + 5, 0, Math.PI * 2);
      ctx.stroke();
    }
    if (uav.avoidance_level > 0) {
      const level = Number(uav.avoidance_level);
      const levelColor = level >= 3 ? "#F87171" : level === 2 ? "#FBBF24" : "#67E8F9";
      text(ctx, `L${level}`, center.x + size + 3, center.y + size + 8, levelColor, Math.max(7, cellSize * 0.25), 700);
    }
  }
}

function contactLabel(contact) {
  if (contact.state === "tracking") return `${contact.contact_id} 持续跟踪`;
  if (contact.state === "confirmed") return `${contact.contact_id} 已确认`;
  if (contact.state === "degraded") return `${contact.contact_id} 跟踪降级`;
  if (contact.state === "lost") return `${contact.contact_id} 暂时丢失`;
  if (contact.state === "departed") return `${contact.contact_id} 已离场`;
  if (contact.vessel_class === "type_ii") return `${contact.contact_id} II 类`;
  if (contact.vessel_class === "type_i") return `${contact.contact_id} I 类`;
  return `${contact.contact_id} 待核查`;
}

function legacyShipLabel(ship) {
  const label = ship.ship_type === "carrier" ? "航母" : "水面目标";
  return `${ship.id} ${label}`;
}

function addLabel(ctx, labels, styles, id, anchor, value, priority, color, selected, markerRadius = 0) {
  const fontSize = 9;
  ctx.font = `600 ${fontSize}px ${FONT}`;
  labels.push({
    id,
    anchor,
    text: value,
    width: ctx.measureText(value).width + 10,
    height: 17,
    priority,
    markerRadius,
  });
  styles.set(id, { color, selected, fontSize });
}

function drawLabelLeader(ctx, anchor, label) {
  const endX = clamp(anchor.x, label.x, label.x + label.width);
  const endY = clamp(anchor.y, label.y, label.y + label.height);
  ctx.beginPath();
  ctx.moveTo(anchor.x, anchor.y);
  ctx.lineTo(endX, endY);
  ctx.stroke();
}

/** Draw all object text after symbols have been placed in screen space. */
export function drawLabels(
  ctx,
  frame,
  cellSize,
  ox,
  oy,
  bounds,
  selectedUavId,
  selectedContactId,
  selectedScenarioVesselId,
  baseCenters,
  showScenario = false,
) {
  const labels = [];
  const styles = new Map();
  const compact = bounds.width < 500;
  const uavs = frame?.uavs || [];
  const contacts = Array.isArray(frame?.contacts) && frame.contacts.length
    ? frame.contacts
    : [];
  const legacyShips = contacts.length ? [] : (frame?.ships || []).filter((ship) => ship?.is_detected);
  const teamMembers = new Set((frame?.teams || []).flatMap((team) => team.members || []));

  for (const uav of uavs) {
    const center = resolveUavDisplayCenter(uav, cellSize, ox, oy, baseCenters);
    const display = uavDisplayState(uav);
    const selected = uav.id === selectedUavId;
    const size = Math.max(5, cellSize * (selected ? .42 : .32));
    const spriteWidth = vesselLength(cellSize, selected);
    const markerRadius = Math.max(
      Math.hypot(spriteWidth, spriteWidth / 3) / 2 + 2,
      size * Math.hypot(.85, 1),
      selected ? size + 5 : 0,
      teamMembers.has(uav.id) ? Math.max(8, cellSize * .6) : 0,
    ) + 1;
    addLabel(
      ctx,
      labels,
      styles,
      `uav:${uav.id}`,
      center,
      compact && !selected ? vehicleDisplayId(uav.id) : `${vehicleDisplayId(uav.id)} · ${display.label}`,
      selected ? "selected" : "uav",
      UAV_STATUS_COLORS[uav.status] || "#334155",
      selected,
      markerRadius,
    );
  }

  for (const contact of contacts) {
    const position = contact.estimated_position;
    if (!Array.isArray(position) || position.length < 2) continue;
    const center = gridCenter(Number(position[0]), Number(position[1]), cellSize, ox, oy);
    const classified = contact.vessel_class === "type_i" || contact.vessel_class === "type_ii";
    const selected = contact.contact_id === selectedContactId;
    addLabel(
      ctx,
      labels,
      styles,
      `contact:${contact.contact_id}`,
      center,
      contactLabel(contact),
      selected ? "selected" : contact.state === "tracking" ? 3.5 : classified ? "classified" : "contact",
      contactColor(contact),
      selected,
      Math.max(vesselLength(cellSize, selected, true) * .53, Math.max(4, cellSize * (selected ? .34 : .25)) + (selected ? 9.5 : 4)),
    );
  }

  for (const ship of legacyShips) {
    const position = ship.position || ship.estimated_position;
    if (!Array.isArray(position) || position.length < 2) continue;
    const carrier = ship.ship_type === "carrier";
    const size = Math.max(4, cellSize * (carrier ? .38 : .28));
    const spriteWidth = carrier ? Math.max(22, size * 4.6) : Math.max(6.5, size * .82);
    const spriteHeight = spriteWidth * (carrier ? 842 / 1472 : 1290 / 224);
    addLabel(
      ctx,
      labels,
      styles,
      `ship:${ship.id}`,
      gridCenter(Number(position[0]), Number(position[1]), cellSize, ox, oy),
      legacyShipLabel(ship),
      "contact",
      "#334155",
      false,
      Math.max(Math.hypot(spriteWidth, spriteHeight) / 2, size + 9) + 1,
    );
  }

  if (showScenario) {
    for (const vessel of frame?.scenario_vessels || []) {
      const position = vessel.position;
      if (!Array.isArray(position) || position.length < 2) continue;
      const selected = vessel.scenario_entity_id === selectedScenarioVesselId;
      addLabel(
        ctx,
        labels,
        styles,
        `scenario:${vessel.scenario_entity_id}`,
        gridCenter(Number(position[0]), Number(position[1]), cellSize, ox, oy),
        `场景 · ${vessel.scenario_entity_id}`,
        selected ? "selected" : "scenario",
        vessel.vessel_class === "type_ii" ? "#B45309" : "#0369A1",
        selected,
        Math.max(4, cellSize * (selected ? .34 : .27)) + (selected ? 6 : 4),
      );
    }
  }

  for (const [index, base] of (frame?.bases || []).entries()) {
    const center = baseCenters?.[index];
    if (!center) continue;
    addLabel(
      ctx,
      labels,
      styles,
      `base:${base.id || index}`,
      center,
      `B${base.number || index + 1}`,
      "uav",
      "#991B1B",
      false,
      Math.max(8, cellSize * .48) + 1,
    );
  }

  for (const region of frame?.search_regions || []) {
    const cell = regionLabelCell(region);
    if (!cell) continue;
    const color = ownerColor(region.assigned_uav_id);
    const value = `区 ${region.assigned_uav_id || region.id}${cellSize >= 14 ? ` ${Math.round(region.completion_pct || 0)}%` : ""}`;
    const anchor = gridCenter(cell[0], cell[1], cellSize, ox, oy);
    anchor.y = Math.max(anchor.y, oy + 24);
    addLabel(ctx, labels, styles, `region:${region.id}`, anchor, value, -1, color, false);
  }

  // Reserve every symbol before priority placement, including later mission-overlay rings.
  const reserved = labels.filter((label) => label.markerRadius > 0).map(({ anchor, markerRadius }) => ({
    x: anchor.x - markerRadius, y: anchor.y - markerRadius, width: markerRadius * 2, height: markerRadius * 2,
  }));
  for (const marker of frame?.markers || []) {
    if (frame.sim_time_min - marker.created_time_min > 60 || !Array.isArray(marker.position)) continue;
    const center = gridCenter(marker.position[0], marker.position[1], cellSize, ox, oy);
    reserved.push({ x: center.x - 6, y: center.y - 6, width: 12, height: 12 });
  }
  // Background annotations predate the object label pass and must also reserve screen space.
  reserved.push({ x: ox, y: oy, width: Math.max(0, bounds.x + bounds.width - ox), height: 24 });
  for (const obstacle of frame?.obstacles || []) {
    const vertices = obstacle.vertices || [];
    if (!vertices.length) continue;
    const center = vertices.reduce((sum, point) => [sum[0] + point[0] / vertices.length, sum[1] + point[1] / vertices.length], [0, 0]);
    const point = coordToPixel(center[0], center[1], cellSize, ox, oy);
    const size = Math.max(7, cellSize * 0.25);
    ctx.font = `700 ${size}px ${FONT}`;
    reserved.push({ x: point.x + 1, y: point.y + 1, width: ctx.measureText(obstacle.label || obstacle.id || "ISLAND").width + 4, height: size + 5 });
  }
  const placed = layoutLabels(labels, bounds, reserved);
  for (const label of placed) {
    if (label.hidden) continue;
    const source = labels.find((item) => item.id === label.id);
    const style = styles.get(label.id);
    if (!source || !style) continue;
    ctx.save();
    if (style.selected) {
      ctx.strokeStyle = `${style.color}B8`;
      ctx.lineWidth = 1;
      drawLabelLeader(ctx, label.anchor, label);
      ctx.stroke();
    }
    const regionLabel = label.id.startsWith("region:");
    ctx.fillStyle = regionLabel ? "rgba(8, 35, 56, .86)" : style.selected ? "rgba(255, 255, 255, .98)" : "rgba(255, 255, 255, .88)";
    ctx.strokeStyle = style.selected || regionLabel ? style.color : "rgba(100, 116, 139, .44)";
    ctx.lineWidth = style.selected ? 1.2 : 0.7;
    ctx.fillRect(label.x, label.y, label.width, label.height);
    ctx.strokeRect(label.x, label.y, label.width, label.height);
    text(ctx, source.text, label.x + 5, label.y + 12, style.color, style.fontSize, style.selected ? 700 : 600);
    ctx.restore();
  }
  return placed;
}

export function drawTransparencyLegend(ctx, bounds) {
  if (!bounds || bounds.width < 128) return;
  const swatches = [
    { color: "#D97706", label: "TASK CELLS" },
    { color: "#0F766E", label: "FRESH INFO" },
    { color: "#38BDF8", label: "UUV SCAN RANGE", shape: "circle" },
    { color: "#DC2626", label: "NO-FLY STORM" },
    { color: "#0E7490", label: "SHIP RADAR" },
    { color: "#2563EB", label: "UUV TRANSIT" },
    { color: "#BE123C", label: "TARGET CONTACT" },
    { color: "#DC2626", label: "BASE STAR" },
    { color: "#334155", label: "TASK BORDER" },
  ];
  const horizontalInset = 8;
  const width = Math.max(128, bounds.width - horizontalInset * 2);
  const availableHeight = bounds.height;
  const itemHeight = clamp(Math.floor((availableHeight - 32) / swatches.length), 13, 17);
  const swatchSize = clamp(Math.floor(itemHeight * 0.58), 7, 10);
  const titleSize = clamp(Math.floor(width * 0.055), 7, 8);
  const labelSize = clamp(Math.floor(width * 0.045), 6, 7);
  const height = 24 + swatches.length * itemHeight + 8;
  if (height > availableHeight) return;
  const x = bounds.x + horizontalInset;
  const y = bounds.y + 10;
  ctx.fillStyle = "rgba(255, 255, 255, .94)";
  ctx.strokeStyle = "rgba(71, 85, 105, .72)";
  ctx.lineWidth = 1;
  ctx.fillRect(x, y, width, height);
  ctx.strokeRect(x, y, width, height);
  text(ctx, "MAP LEGEND", x + 8, y + 13, "#334155", titleSize, 700);
  swatches.forEach((swatch, index) => {
    const itemX = x + 9;
    const itemY = y + 22 + index * itemHeight;
    ctx.fillStyle = swatch.color;
    ctx.strokeStyle = "#64748B";
    if (swatch.shape === "strip") {
      ctx.fillRect(itemX, itemY + swatchSize * 0.2, swatchSize, swatchSize * 0.6);
      ctx.strokeRect(itemX, itemY + swatchSize * 0.2, swatchSize, swatchSize * 0.6);
    } else if (swatch.shape === "cone") {
      ctx.beginPath();
      ctx.moveTo(itemX, itemY + swatchSize);
      ctx.lineTo(itemX + swatchSize, itemY + swatchSize * 0.15);
      ctx.lineTo(itemX + swatchSize, itemY + swatchSize);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    } else if (swatch.shape === "circle") {
      ctx.beginPath();
      ctx.arc(itemX + swatchSize / 2, itemY + swatchSize / 2, swatchSize * 0.42, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(56, 189, 248, .16)";
      ctx.fill();
      ctx.stroke();
    } else {
      ctx.fillRect(itemX, itemY, swatchSize, swatchSize);
      ctx.strokeRect(itemX, itemY, swatchSize, swatchSize);
    }
    text(ctx, swatch.label, itemX + swatchSize + 4, itemY + swatchSize - 1, "#475569", labelSize, 600);
  });
}

export function drawHoverTooltip(ctx, hover, cellSize, ox, oy, width, height) {
  if (!hover) return;
  const point = coordToPixel(hover.col, hover.row, cellSize, ox, oy);
  ctx.strokeStyle = "#0F172A";
  ctx.lineWidth = 1.5;
  ctx.strokeRect(point.x, point.y, cellSize, cellSize);
  if (width <= 620) return;
  const tipWidth = Math.min(188, width - 16);
  const tipHeight = 66;
  const right = point.x + cellSize + 8;
  const x = right + tipWidth <= width - 8 ? right : Math.max(8, point.x - tipWidth - 8);
  const y = Math.max(8, Math.min(height - tipHeight - 8, point.y));
  ctx.fillStyle = "rgba(255, 255, 255, .97)";
  ctx.strokeStyle = "#64748B";
  ctx.fillRect(x, y, tipWidth, tipHeight);
  ctx.strokeRect(x, y, tipWidth, tipHeight);
  text(ctx, `CELL ${String(hover.col).padStart(2, "0")} / ${String(hover.row).padStart(2, "0")}`, x + 9, y + 17, "#0F172A", 11);
  text(ctx, `扫描新鲜度  ${(hover.I * 100).toFixed(1)}%`, x + 9, y + 35, "#475569", 10);
  text(ctx, `目标线索    ${(hover.V * 100).toFixed(1)}%`, x + 9, y + 52, "#475569", 10);
}

export function renderFrame(ctx, frame, options = {}) {
  const {
    cellSize,
    offsetX,
    offsetY,
    showGrid,
    hoverInfo,
    selectedUavId,
    frameCount = 0,
    assets,
    mapBounds,
    legendBounds,
    trailMode = "tail",
    selectedContactId,
    showScenario = false,
    selectedScenarioVesselId,
  } = options;
  const gridCols = Number.isFinite(Number(options.gridCols))
    ? Number(options.gridCols)
    : Math.max(1, frame?.info_matrix?.length || 30);
  const gridRows = Number.isFinite(Number(options.gridRows))
    ? Number(options.gridRows)
    : Math.max(1, frame?.info_matrix?.[0]?.length || 30);
  const width = ctx.canvas.clientWidth || ctx.canvas.width;
  const height = ctx.canvas.clientHeight || ctx.canvas.height;
  const bases = normalizeBases(frame?.bases, frame?.base_position);
  const fallbackBounds = {
    x: offsetX,
    y: offsetY,
    width: gridCols * cellSize,
    height: gridRows * cellSize,
  };
  const resolvedMapBounds = mapBounds || fallbackBounds;
  const baseCenters = buildBaseCenters(bases, resolvedMapBounds, gridRows);
  drawBackground(
    ctx,
    width,
    height,
    cellSize,
    offsetX,
    offsetY,
    resolvedMapBounds,
    assets,
    gridCols,
    gridRows,
    frame?.task_area,
  );
  if (frame) {
    drawHeatmap(ctx, frame.info_matrix, frame.value_matrix, cellSize, offsetX, offsetY, gridCols, gridRows);
    drawTargetInformation(ctx, frame.target_info_matrix, cellSize, offsetX, offsetY, gridCols, gridRows);
    drawSearchRegions(ctx, frame.search_regions, cellSize, offsetX, offsetY, selectedUavId);
    drawGridLines(ctx, cellSize, offsetX, offsetY, showGrid, gridCols, gridRows);
    drawObstacles(ctx, frame.obstacles, cellSize, offsetX, offsetY, frameCount);
    drawIntents(ctx, frame.intents, frame.intent_statuses, cellSize, offsetX, offsetY);
    const contacts = Array.isArray(frame.contacts) && frame.contacts.length
      ? frame.contacts : frame.ships;
    drawTrackRegions(ctx, frame.track_regions, contacts, cellSize, offsetX, offsetY);
    drawPassiveEvidence(ctx, frame.evidence, cellSize, offsetX, offsetY, frameCount);
    drawUavTrails(ctx, frame.uavs, cellSize, offsetX, offsetY, selectedUavId, trailMode);
    drawPaths(ctx, frame.uavs, cellSize, offsetX, offsetY, selectedUavId, baseCenters);
    drawUavScanRanges(ctx, frame.uavs, cellSize, offsetX, offsetY, baseCenters, selectedUavId);
    drawMarkers(ctx, frame.markers, cellSize, offsetX, offsetY, frame.sim_time_min, frameCount);
    if (Array.isArray(frame.contacts) && frame.contacts.length) {
      drawContacts(ctx, frame.contacts, cellSize, offsetX, offsetY, selectedContactId, frameCount, assets);
    } else {
      drawShips(ctx, frame.ships, cellSize, offsetX, offsetY, assets, gridCols, gridRows);
    }
    if (showScenario) {
      drawScenarioVessels(ctx, frame.scenario_vessels, cellSize, offsetX, offsetY, selectedScenarioVesselId);
    }
    drawUavs(ctx, frame.uavs, cellSize, offsetX, offsetY, selectedUavId, assets, baseCenters);
    drawBases(ctx, bases, baseCenters, cellSize, frameCount);
    drawLabels(
      ctx,
      frame,
      cellSize,
      offsetX,
      offsetY,
      resolvedMapBounds,
      selectedUavId,
      selectedContactId,
      selectedScenarioVesselId,
      baseCenters,
      showScenario,
    );
    drawTransparencyLegend(ctx, legendBounds);
  }
  drawHoverTooltip(ctx, hoverInfo, cellSize, offsetX, offsetY, width, height);
}
