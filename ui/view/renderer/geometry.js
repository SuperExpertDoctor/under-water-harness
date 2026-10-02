/**
 * 网格坐标 ↔ Canvas 像素坐标映射。
 *
 * cellSize  = task square size / max(grid columns, grid rows)
 * offsetX   = horizontal origin of the task grid
 * offsetY   = vertical origin of the task grid
 */

const BACKGROUND_ASPECT_RATIO = 1672 / 938;
const DEFAULT_GRID_CELLS = 30;

export function computeLayout(
  canvasW,
  canvasH,
  gridCols = DEFAULT_GRID_CELLS,
  gridRows = DEFAULT_GRID_CELLS,
  { includeLegend = true } = {},
) {
  const cols = Number.isFinite(Number(gridCols)) && Number(gridCols) > 0
    ? Math.floor(Number(gridCols))
    : DEFAULT_GRID_CELLS;
  const rows = Number.isFinite(Number(gridRows)) && Number(gridRows) > 0
    ? Math.floor(Number(gridRows))
    : DEFAULT_GRID_CELLS;
  const inset = canvasW <= 620 ? 12 : 16;
  const legendWidth = includeLegend && canvasW >= 900 ? 168 : 0;
  const availableWidth = Math.max(1, canvasW - inset * 2 - legendWidth - (legendWidth ? 10 : 0));
  const availableHeight = Math.max(1, canvasH - inset * 2);
  const aspectRatio = canvasW <= 620 ? 1.05 : BACKGROUND_ASPECT_RATIO;
  const chartWidth = Math.min(availableWidth, availableHeight * aspectRatio);
  const chartHeight = chartWidth / aspectRatio;
  const groupWidth = chartWidth + (legendWidth ? legendWidth + 10 : 0);
  const chartX = Math.max(inset, (canvasW - groupWidth) / 2);
  const chartY = inset;
  const taskInset = Math.max(5, Math.min(12, Math.round(chartHeight * 0.025)));
  const taskSize = Math.max(
    Math.max(cols, rows),
    Math.floor(Math.min(chartHeight - taskInset * 2, chartWidth - taskInset * 2)),
  );
  const cellSize = taskSize / Math.max(cols, rows);
  const taskWidth = cellSize * cols;
  const taskHeight = cellSize * rows;
  // Keep the square surveillance sector in open water, away from the
  // mainland occupying the left edge of the 16:9 chart.
  const offsetX = chartX + chartWidth - taskWidth - taskInset;
  const offsetY = chartY + (chartHeight - taskHeight) / 2;
  const mapBounds = { x: chartX, y: chartY, width: chartWidth, height: chartHeight };
  const legendBounds = legendWidth
    ? { x: chartX + chartWidth + 10, y: chartY, width: legendWidth, height: chartHeight }
    : null;

  return {
    cellSize,
    gridCols: cols,
    gridRows: rows,
    offsetX,
    offsetY,
    taskBounds: { x: offsetX, y: offsetY, width: taskWidth, height: taskHeight },
    mapBounds,
    legendBounds,
  };
}

export function coordToPixel(col, row, cellSize, offsetX, offsetY) {
  return {
    x: offsetX + col * cellSize,
    y: offsetY + row * cellSize,
  };
}

export function pixelToCoord(px, py, cellSize, offsetX, offsetY, cols = DEFAULT_GRID_CELLS, rows = DEFAULT_GRID_CELLS) {
  const col = Math.floor((px - offsetX) / cellSize);
  const row = Math.floor((py - offsetY) / cellSize);
  if (col < 0 || col >= cols || row < 0 || row >= rows) return null;
  return { col, row };
}

/**
 * Ctrl+scroll zoom state: base-layout pixels map to screen as
 * screen = base * a + b (per axis). `zoomViewAt` re-anchors the transform
 * on the cursor; `zoomedLayout` applies it to every layout quantity so
 * renderers and hit tests stay consistent.
 */
export function zoomViewAt(view, cx, cy, deltaY, { min = 1, max = 8 } = {}) {
  const next = Math.min(max, Math.max(min, view.a * Math.exp(-deltaY * 0.0016)));
  if (next === view.a) return view;
  const r = next / view.a;
  const bx = (view.bx - cx) * r + cx;
  const by = (view.by - cy) * r + cy;
  return next === min ? { a: 1, bx: 0, by: 0 } : { a: next, bx, by };
}

export function zoomedLayout(layout, view) {
  if (!layout || !view || view.a === 1) return layout;
  const { a, bx, by } = view;
  const rect = (bounds) => bounds && {
    x: bounds.x * a + bx,
    y: bounds.y * a + by,
    width: bounds.width * a,
    height: bounds.height * a,
  };
  return {
    ...layout,
    cellSize: layout.cellSize * a,
    offsetX: layout.offsetX * a + bx,
    offsetY: layout.offsetY * a + by,
    mapBounds: rect(layout.mapBounds),
    taskBounds: rect(layout.taskBounds),
    legendBounds: rect(layout.legendBounds),
  };
}

/** Convert a CSS-pixel drag into a half-open, clamped grid rectangle. */
export function dragToBBox(start, end, layout, cols = DEFAULT_GRID_CELLS, rows = DEFAULT_GRID_CELLS) {
  if (!start || !end || !layout || Math.abs(start.x - end.x) < 3 || Math.abs(start.y - end.y) < 3) {
    return null;
  }
  const clamp = (value, maximum) => Math.max(0, Math.min(maximum, value));
  const x0 = (Math.min(start.x, end.x) - layout.offsetX) / layout.cellSize;
  const y0 = (Math.min(start.y, end.y) - layout.offsetY) / layout.cellSize;
  const x1 = (Math.max(start.x, end.x) - layout.offsetX) / layout.cellSize;
  const y1 = (Math.max(start.y, end.y) - layout.offsetY) / layout.cellSize;
  const bbox = [
    clamp(Math.floor(x0), cols),
    clamp(Math.floor(y0), rows),
    clamp(Math.ceil(x1), cols),
    clamp(Math.ceil(y1), rows),
  ];
  return bbox[0] < bbox[2] && bbox[1] < bbox[3] ? bbox : null;
}
