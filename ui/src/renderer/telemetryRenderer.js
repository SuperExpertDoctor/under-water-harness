const FONT = '"Fira Code", "Microsoft YaHei", monospace';
const UUV_SCAN_RADIUS_CELLS = 2;
const STATUS_COLORS = {
  searching: "#0f766e",
  tracking: "#be123c",
  returning: "#c2410c",
  holding: "#a16207",
  idle: "#475569",
  transit: "#1d4ed8",
  failed: "#7f1d1d",
};

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

function gridResolution(frame) {
  const cellSize = Number(frame?.task_area?.cell_size_km);
  const width = Number(frame?.task_area?.width_km);
  const height = Number(frame?.task_area?.height_km);
  return {
    cols: frame?.info_matrix?.length || Math.round(width / cellSize) || 30,
    rows: frame?.info_matrix?.[0]?.length || Math.round(height / cellSize) || 30,
  };
}

export function createLayout(width, height, frame) {
  const { cols, rows } = gridResolution(frame);
  const inset = width < 650 ? 12 : 22;
  const chartWidth = Math.max(1, width - inset * 2);
  const chartHeight = Math.max(1, height - inset * 2);
  const cellSize = Math.max(1, Math.min(chartWidth / cols, chartHeight / rows));
  const taskWidth = cellSize * cols;
  const taskHeight = cellSize * rows;
  return {
    cols,
    rows,
    cellSize,
    offsetX: (width - taskWidth) / 2,
    offsetY: (height - taskHeight) / 2,
    width,
    height,
  };
}

function center(position, layout) {
  if (!Array.isArray(position) || position.length < 2) return null;
  return {
    x: layout.offsetX + (Number(position[0]) + 0.5) * layout.cellSize,
    y: layout.offsetY + (Number(position[1]) + 0.5) * layout.cellSize,
  };
}

function text(ctx, value, x, y, color, size, weight = 600) {
  ctx.font = `${weight} ${size}px ${FONT}`;
  ctx.fillStyle = color;
  ctx.fillText(value, x, y);
}

function drawMap(ctx, layout, image, frame) {
  ctx.fillStyle = "#e8f5f8";
  ctx.fillRect(0, 0, layout.width, layout.height);
  if (image?.complete && image.naturalWidth) {
    ctx.globalAlpha = 0.95;
    ctx.drawImage(image, 0, 0, image.naturalWidth, image.naturalHeight, 0, 0, layout.width, layout.height);
    ctx.globalAlpha = 1;
  }
  ctx.strokeStyle = "#0b3857";
  ctx.lineWidth = 2;
  ctx.strokeRect(layout.offsetX - 1, layout.offsetY - 1, layout.cols * layout.cellSize + 2, layout.rows * layout.cellSize + 2);
  const width = Number(frame?.task_area?.width_km);
  const height = Number(frame?.task_area?.height_km);
  const label = Number.isFinite(width) && Number.isFinite(height)
    ? `${Math.round(width)} × ${Math.round(height)} km`
    : "TASK AREA";
  ctx.fillStyle = "rgba(255, 255, 255, .86)";
  ctx.fillRect(layout.offsetX + 6, layout.offsetY + 6, Math.min(layout.cols * layout.cellSize - 12, 180), 22);
  text(ctx, `任务区域 / ${label}`, layout.offsetX + 12, layout.offsetY + 21, "#0b3857", 10, 700);
}

function drawInformation(ctx, frame, layout) {
  for (let col = 0; col < layout.cols; col += 1) {
    for (let row = 0; row < layout.rows; row += 1) {
      const freshness = clamp(Number(frame?.info_matrix?.[col]?.[row] || 0), 0, 1);
      const value = clamp(Number(frame?.value_matrix?.[col]?.[row] || 0), 0, 1);
      const x = layout.offsetX + col * layout.cellSize;
      const y = layout.offsetY + row * layout.cellSize;
      if (freshness > 0.7) ctx.fillStyle = `rgba(13, 148, 136, ${0.14 + freshness * 0.2})`;
      else if (freshness >= 0.2) ctx.fillStyle = `rgba(217, 119, 6, ${0.08 + freshness * 0.15})`;
      else ctx.fillStyle = `rgba(37, 99, 235, ${0.02 + value * 0.05})`;
      ctx.fillRect(x + 0.5, y + 0.5, Math.max(0, layout.cellSize - 1), Math.max(0, layout.cellSize - 1));
      ctx.fillStyle = `rgba(15, 23, 42, ${0.1 - freshness * 0.08})`;
      ctx.fillRect(x, y, layout.cellSize, layout.cellSize);
    }
  }
}

function drawGrid(ctx, layout) {
  for (let index = 0; index <= Math.max(layout.cols, layout.rows); index += 1) {
    const major = index % 5 === 0;
    ctx.strokeStyle = major ? "rgba(30, 64, 88, .36)" : "rgba(71, 85, 105, .18)";
    ctx.lineWidth = major ? 0.9 : 0.5;
    ctx.beginPath();
    if (index <= layout.cols) {
      ctx.moveTo(layout.offsetX + index * layout.cellSize, layout.offsetY);
      ctx.lineTo(layout.offsetX + index * layout.cellSize, layout.offsetY + layout.rows * layout.cellSize);
    }
    if (index <= layout.rows) {
      ctx.moveTo(layout.offsetX, layout.offsetY + index * layout.cellSize);
      ctx.lineTo(layout.offsetX + layout.cols * layout.cellSize, layout.offsetY + index * layout.cellSize);
    }
    ctx.stroke();
  }
}

function drawTrail(ctx, uav, layout, selected) {
  const trail = Array.isArray(uav?.trail) ? uav.trail : [];
  if (trail.length < 2) return;
  const color = STATUS_COLORS[uav.status] || "#475569";
  ctx.save();
  ctx.strokeStyle = color;
  ctx.globalAlpha = selected ? 0.9 : 0.55;
  ctx.lineWidth = selected ? 2.4 : 1.5;
  ctx.lineCap = "round";
  ctx.beginPath();
  trail.forEach((point, index) => {
    const item = center(point, layout);
    if (!item) return;
    if (index === 0) ctx.moveTo(item.x, item.y);
    else ctx.lineTo(item.x, item.y);
  });
  ctx.stroke();
  ctx.restore();
}

function drawScanRange(ctx, uav, layout, selected) {
  const item = center(uav?.position, layout);
  if (!item || uav?.operational_status === "failed") return;
  const radius = UUV_SCAN_RADIUS_CELLS * layout.cellSize;
  const idle = uav.status === "idle";
  ctx.save();
  ctx.beginPath();
  ctx.arc(item.x, item.y, radius, 0, Math.PI * 2);
  ctx.fillStyle = selected ? "rgba(14, 165, 233, .17)" : idle ? "rgba(148, 163, 184, .05)" : "rgba(14, 165, 233, .09)";
  ctx.strokeStyle = selected ? "rgba(125, 211, 252, .98)" : idle ? "rgba(148, 163, 184, .5)" : "rgba(56, 189, 248, .72)";
  ctx.lineWidth = selected ? 2 : 1.2;
  ctx.setLineDash(selected || !idle ? [] : [5, 4]);
  ctx.fill();
  ctx.stroke();
  ctx.restore();
}

function drawUuv(ctx, uav, layout, image, selected) {
  const item = center(uav?.position, layout);
  if (!item) return;
  const color = STATUS_COLORS[uav.status] || "#475569";
  const width = Math.max(13, layout.cellSize * (selected ? 0.54 : 0.43));
  const height = image?.naturalWidth ? width * image.naturalHeight / image.naturalWidth : width * 1.5;
  ctx.save();
  ctx.globalAlpha = uav.operational_status === "failed" ? 0.42 : 1;
  if (image?.complete && image.naturalWidth) {
    ctx.drawImage(image, item.x - width / 2, item.y - height / 2, width, height);
  } else {
    ctx.translate(item.x, item.y);
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.moveTo(width * 0.65, 0);
    ctx.lineTo(-width * 0.42, -width * 0.3);
    ctx.lineTo(-width * 0.62, 0);
    ctx.lineTo(-width * 0.42, width * 0.3);
    ctx.closePath();
    ctx.fill();
  }
  ctx.restore();
  if (selected) {
    ctx.strokeStyle = "#0f172a";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(item.x, item.y, width * 0.76, 0, Math.PI * 2);
    ctx.stroke();
  }
  const id = String(uav.id || "").replace(/^UAV-/i, "UUV-");
  text(ctx, id, item.x + width * 0.45, item.y - width * 0.4, color, Math.max(8, Math.min(11, layout.cellSize * 0.28)), 700);
}

export function renderTelemetry(ctx, frame, layout, assets, selectedUavId) {
  if (!layout) return;
  drawMap(ctx, layout, assets.background, frame);
  if (!frame) return;
  drawInformation(ctx, frame, layout);
  drawGrid(ctx, layout);
  for (const uav of frame.uavs || []) drawTrail(ctx, uav, layout, uav.id === selectedUavId);
  for (const uav of frame.uavs || []) drawScanRange(ctx, uav, layout, uav.id === selectedUavId);
  for (const uav of frame.uavs || []) drawUuv(ctx, uav, layout, assets.uuv, uav.id === selectedUavId);
}

export function findUuvAtPoint(frame, layout, x, y) {
  return (frame?.uavs || [])
    .map((uav) => {
      const item = center(uav.position, layout);
      return item ? { uav, distance: Math.hypot(x - item.x, y - item.y) } : null;
    })
    .filter(Boolean)
    .sort((left, right) => left.distance - right.distance)[0];
}

export function interpolateFrame(previous, current, progress) {
  if (!current) return null;
  const oldById = new Map((previous?.uavs || []).map((uav) => [uav.id, uav]));
  return {
    ...current,
    uavs: (current.uavs || []).map((uav) => {
      const old = oldById.get(uav.id);
      if (!old || !Array.isArray(old.position) || !Array.isArray(uav.position)) return uav;
      return {
        ...uav,
        position: [
          Number(old.position[0]) + (Number(uav.position[0]) - Number(old.position[0])) * progress,
          Number(old.position[1]) + (Number(uav.position[1]) - Number(old.position[1])) * progress,
        ],
      };
    }),
  };
}

export { STATUS_COLORS, UUV_SCAN_RADIUS_CELLS };
