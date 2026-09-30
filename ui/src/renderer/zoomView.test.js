import assert from "node:assert/strict";
import test from "node:test";
import { computeLayout, pixelToCoord, zoomedLayout, zoomViewAt } from "./geometry.js";

test("ctrl+scroll keeps the world point under the cursor fixed", () => {
  const layout = computeLayout(800, 600, 40, 40, { includeLegend: false });
  const cursor = { x: layout.offsetX + layout.cellSize * 10.5, y: layout.offsetY + layout.cellSize * 20.5 };
  const before = pixelToCoord(cursor.x, cursor.y, layout.cellSize, layout.offsetX, layout.offsetY, 40, 40);
  let view = { a: 1, bx: 0, by: 0 };
  for (let i = 0; i < 5; i += 1) view = zoomViewAt(view, cursor.x, cursor.y, -120);
  assert.ok(view.a > 1);
  const zoomed = zoomedLayout(layout, view);
  assert.equal(zoomed.cellSize, layout.cellSize * view.a);
  const after = pixelToCoord(cursor.x, cursor.y, zoomed.cellSize, zoomed.offsetX, zoomed.offsetY, 40, 40);
  assert.deepEqual(after, before);
});

test("zoom caps at 8x and zooming fully out resets to the fitted view", () => {
  const cursor = { x: 400, y: 300 };
  let view = { a: 1, bx: 0, by: 0 };
  for (let i = 0; i < 200; i += 1) view = zoomViewAt(view, cursor.x, cursor.y, -500);
  assert.equal(view.a, 8);
  for (let i = 0; i < 200; i += 1) view = zoomViewAt(view, cursor.x, cursor.y, 500);
  assert.deepEqual(view, { a: 1, bx: 0, by: 0 });
});

test("zoomed layout scales every drawn quantity about the same transform", () => {
  const layout = computeLayout(1000, 600, 40, 40, { includeLegend: true });
  const view = zoomViewAt({ a: 1, bx: 0, by: 0 }, 100, 50, -200);
  const zoomed = zoomedLayout(layout, view);
  const { a, bx, by } = view;
  assert.equal(zoomed.offsetX, layout.offsetX * a + bx);
  assert.equal(zoomed.offsetY, layout.offsetY * a + by);
  assert.equal(zoomed.mapBounds.x, layout.mapBounds.x * a + bx);
  assert.equal(zoomed.mapBounds.width, layout.mapBounds.width * a);
  assert.equal(zoomed.taskBounds.y, layout.taskBounds.y * a + by);
  assert.equal(zoomed.taskBounds.height, layout.taskBounds.height * a);
  assert.equal(zoomed.legendBounds.x, layout.legendBounds.x * a + bx);
  assert.equal(zoomedLayout(layout, { a: 1, bx: 0, by: 0 }), layout);
});
