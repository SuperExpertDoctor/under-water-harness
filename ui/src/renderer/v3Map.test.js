import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());
const sprite = { complete: true, naturalWidth: 2172, naturalHeight: 724 };
function canvas() {
  const calls = { images: [], rotations: [], fills: [], strokes: [] };
  const ctx = new Proxy({ calls, canvas: { width: 800, height: 600 },
    drawImage(...args) { calls.images.push(args); }, rotate(angle) { calls.rotations.push(angle); },
    fillRect(...args) { calls.fills.push({ color: ctx.fillStyle, rect: args }); },
    stroke() { calls.strokes.push(ctx.strokeStyle); },
    measureText(value) { return { width: value.length * 6 }; },
  }, { get: (target, key) => key in target ? target[key] : () => {} });
  return ctx;
}

for (const deg of [0, 90, 180, 270, 359]) {
  test(`friendly left-facing sprite agrees with world heading ${deg}`, async () => {
    const { drawUavs } = await server.ssrLoadModule("/src/renderer/layers.js");
    const ctx = canvas();
    drawUavs(ctx, [{ id: "UUV-1", position: [2, 3], heading_deg: deg, status: "searching" }], 15, 0, 0, null, { uav: sprite }, []);
    const rotation = ctx.calls.rotations.at(-1);
    const theta = deg * Math.PI / 180;
    assert.ok(Math.abs(-Math.cos(rotation) - Math.cos(theta)) < 1e-9);
    assert.ok(Math.abs(-Math.sin(rotation) + Math.sin(theta)) < 1e-9);
    const args = ctx.calls.images[0];
    assert.deepEqual(args.slice(1, 5), [0, 0, 2172, 724]);
    assert.equal(args[7] / args[8], 3);
  });
}

for (const velocity of [[1, 0], [0, -1], [-1, 0], [0, 1]]) {
  test(`submarine follows observed screen velocity ${velocity}`, async () => {
    const { drawContacts } = await server.ssrLoadModule("/src/renderer/layers.js");
    const ctx = canvas();
    drawContacts(ctx, [{ contact_id: "C1", estimated_position: [5, 8], estimated_velocity: velocity, state: "tracking" }], 15, 0, 0, null, 0, { submarine: sprite });
    assert.equal(ctx.calls.images.length, 1);
    const angle = ctx.calls.rotations.at(-1);
    assert.ok(Math.abs(-Math.cos(angle) - velocity[0]) < 1e-9);
    assert.ok(Math.abs(-Math.sin(angle) - velocity[1]) < 1e-9);
  });
}

test("undetected target is never drawn from truth or an asset alone", async () => {
  const { renderFrame } = await server.ssrLoadModule("/src/renderer/layers.js");
  const ctx = canvas();
  renderFrame(ctx, { contacts: [], targets: [{ pose: [100, 100, 0] }], uavs: [] }, { cellSize: 10, offsetX: 0, offsetY: 0, gridCols: 40, gridRows: 40, assets: { submarine: sprite } });
  assert.equal(ctx.calls.images.length, 0);
});

test("eight owners have distinct stable outlines without region fills", async () => {
  const { drawSearchRegions } = await server.ssrLoadModule("/src/renderer/layers.js");
  const regions = Array.from({ length: 8 }, (_, i) => ({ id: `R${i}`, assigned_uav_id: `UUV-${i + 1}`, cells: [[i, 0]] }));
  const ctx = canvas();
  drawSearchRegions(ctx, regions, 10, 0, 0);
  assert.equal(ctx.calls.fills.length, 0);
  assert.equal(new Set(ctx.calls.strokes).size, 8);
  assert.ok(ctx.calls.strokes.length >= 8);
});

test("search path uses the same owner palette as the region", async () => {
  const { drawPaths, drawSearchRegions } = await server.ssrLoadModule("/src/renderer/layers.js");
  const ctx = canvas();
  drawSearchRegions(ctx, [{ id: "r", assigned_uav_id: "UUV-5", cells: [[0, 0]] }], 10, 0, 0);
  const color = ctx.calls.strokes[0].slice(0, 7);
  drawPaths(ctx, [{ id: "UUV-5", status: "searching", planned_path: [[0, 0], [2, 2]] }], 10, 0, 0, null, []);
  assert.ok(ctx.calls.strokes.at(-1).startsWith(color));
});

test("fractional zoom information cells retain consistent one-pixel gutters", async () => {
  const { drawHeatmap } = await server.ssrLoadModule("/src/renderer/layers.js");
  const ctx = canvas();
  drawHeatmap(ctx, [[1], [1]], [], 12.525, 1.3, 2.7, 2, 1);
  const [first, second] = ctx.calls.fills.map((fill) => fill.rect);
  assert.ok([first[2], first[3], second[2], second[3]].every(value => Number.isInteger(value) && value > 0));
  assert.equal(first[0] + first[2] + 1, second[0]);
});

test("map summary distinguishes search responsibility, transit and established tracking", async () => {
  const module = await server.ssrLoadModule("/src/components/CanvasMap.jsx");
  assert.equal(typeof module.MapSummary, "function");
  const frame = { coverage_pct: 32.7, search_regions: [{ id: "R1", assigned_uav_id: "UUV-1", completion_pct: 15 }],
    uavs: [{ id: "UUV-1", operation_mode: "coverage" }, { id: "UUV-2", operation_mode: "track", task_phase: "transit" },
      { id: "UUV-3", operation_mode: "track", task_phase: "tracking", effective_tracking: true }], contacts: [] };
  const html = renderToStaticMarkup(createElement(module.MapSummary, { frame }));
  for (const text of ["搜索", "跟踪转场", "有效跟踪", "32.7%", "尚未发现", "UUV-1", "15%"])
    assert.ok(html.includes(text), text);
});

test("friendly console does not offer truth or extra-vessel controls", async () => {
  const { default: App } = await server.ssrLoadModule("/src/App.jsx");
  const html = renderToStaticMarkup(createElement(App));
  assert.ok(!html.includes("场景真值"));
});

test("tracking transit and unavailable observation are not mislabeled as effective tracking", async () => {
  const { uavDisplayState } = await server.ssrLoadModule("/src/renderer/displayState.js");
  assert.equal(uavDisplayState({ operation_mode: "track", task_phase: "transit", status: "transit" }).label, "跟踪转场");
  assert.equal(uavDisplayState({ operation_mode: "track", task_phase: "tracking", status: "tracking", effective_tracking: false }).label, "观测中断");
  assert.equal(uavDisplayState({ operation_mode: "idle", task_phase: "exit", status: "transit" }).label, "驶离补换");
});

test("mobile mission square uses available height without stretching the map bitmap", async () => {
  const { computeLayout } = await server.ssrLoadModule("/src/renderer/geometry.js");
  const layout = computeLayout(390, 400, 40, 40, { includeLegend: false });
  assert.ok(layout.taskBounds.width >= 300);
  assert.ok(layout.taskBounds.x >= 0);
  assert.ok(layout.taskBounds.y + layout.taskBounds.height <= 400);
});
