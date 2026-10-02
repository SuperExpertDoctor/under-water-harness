import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { mergeTelemetryFrame } from "../../state/missionState.js";

const server = await createServer({ root: fileURLToPath(new URL("../../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());
function canvas() {
  const calls = { fills: [], strokes: [], texts: [] };
  const ctx = new Proxy({ calls, canvas: { width: 800, height: 600 },
    fillRect(...rect) { calls.fills.push({ color: ctx.fillStyle, rect }); },
    stroke() { calls.strokes.push({ color: ctx.strokeStyle, width: ctx.lineWidth }); },
    fillText(value) { calls.texts.push(value); },
    measureText(value) { return { width: value.length * 6 }; },
  }, { get: (target, key) => key in target ? target[key] : () => {} });
  return ctx;
}
const alpha = (color) => Number(color.match(/,\s*([\d.]+)\)$/)?.[1]);

test("scan tint is continuous and transparent at zero instead of changing categorical hue", async () => {
  const { drawHeatmap } = await server.ssrLoadModule("/src/view/renderer/layers.js");
  const ctx = canvas();
  drawHeatmap(ctx, [[0], [.25], [.5], [1]], [], 12, 0, 0, 4, 1);
  assert.equal(ctx.calls.fills.length, 3);
  assert.deepEqual(ctx.calls.fills.map((fill) => alpha(fill.color)), [.075, .15, .3]);
  assert.equal(new Set(ctx.calls.fills.map((fill) => fill.color.split(",").slice(0, 3).join(","))).size, 1);
  assert.ok(ctx.calls.fills.every((fill) => fill.rect[2] < 12));
});

test("responsibility alone adds no bright area fill and selection strengthens only the border", async () => {
  const { drawSearchRegions } = await server.ssrLoadModule("/src/view/renderer/layers.js");
  const regions = [{ id: "r", assigned_uav_id: "UUV-1", cells: [[0, 0], [1, 0]] }];
  const ordinary = canvas(), selected = canvas();
  drawSearchRegions(ordinary, regions, 12, 0, 0, null);
  drawSearchRegions(selected, regions, 12, 0, 0, "UUV-1");
  assert.equal(ordinary.calls.fills.length, 0);
  assert.equal(selected.calls.fills.length, 0);
  assert.equal(ordinary.calls.strokes[0].width, 1);
  assert.equal(selected.calls.strokes[0].width, 2);
  assert.ok(parseInt(ordinary.calls.strokes[0].color.slice(-2), 16) >= 170);
});

test("responsibility labels use an interior central cell, not the first cell at the top", async () => {
  const { regionLabelCell } = await server.ssrLoadModule("/src/view/renderer/layers.js");
  assert.equal(typeof regionLabelCell, "function");
  const cells = Array.from({ length: 9 }, (_, index) => [20 + index % 3, 25 + Math.floor(index / 3)]);
  assert.deepEqual(regionLabelCell({ cells }), [21, 26]);
  const concave = [[0, 0], [0, 1], [0, 2], [1, 2], [2, 2]];
  assert.ok(concave.some((cell) => JSON.stringify(cell) === JSON.stringify(regionLabelCell({ cells: concave }))));
});

test("target information draws supplied nonzero cells only and never creates synthetic neighbors", async () => {
  const { drawTargetInformation } = await server.ssrLoadModule("/src/view/renderer/layers.js");
  assert.equal(typeof drawTargetInformation, "function");
  const ctx = canvas();
  drawTargetInformation(ctx, [[0, 0], [1, .5]], 12, 0, 0, 2, 2);
  assert.equal(ctx.calls.fills.length, 2);
  assert.deepEqual(ctx.calls.fills.map((fill) => alpha(fill.color)), [.28, .14]);
  const empty = canvas();
  drawTargetInformation(empty, undefined, 12, 0, 0, 2, 2);
  assert.equal(empty.calls.fills.length, 0);
});

test("invalid information cannot yield invalid canvas styles", async () => {
  const { drawHeatmap } = await server.ssrLoadModule("/src/view/renderer/layers.js");
  const ctx = canvas();
  drawHeatmap(ctx, [[NaN], [Infinity], [-1], [5]], [], 12, 0, 0, 4, 1);
  assert.equal(ctx.calls.fills.length, 1);
  assert.equal(alpha(ctx.calls.fills[0].color), .3);
});

test("legend identifies both normalized fields with a numeric scale", async () => {
  const { InformationLegend } = await server.ssrLoadModule("/src/view/components/CanvasMap.jsx");
  assert.equal(typeof InformationLegend, "function");
  const html = renderToStaticMarkup(createElement(InformationLegend));
  for (const label of ["扫描新鲜度", "目标线索", "0", "100%"])
    assert.ok(html.includes(label), label);
});

test("cell readings use separate backend fields and refresh with incoming data", async () => {
  const { readCellInformation } = await server.ssrLoadModule("/src/view/components/CanvasMap.jsx");
  assert.equal(typeof readCellInformation, "function");
  const cell = { col: 0, row: 0 };
  assert.deepEqual(readCellInformation({ info_matrix: [[.5]], target_info_matrix: [[.8]], value_matrix: [[1]] }, cell), { ...cell, I: .5, V: .8 });
  assert.equal(readCellInformation({ info_matrix: [[.25]] }, cell).I, .25);
  assert.equal(readCellInformation({ info_matrix: [[.25]] }, cell).V, 0);
  assert.equal(readCellInformation({}, null), null);
});

test("telemetry merges new fields but drops target evidence on episode change", () => {
  const first = { episode_id: "a", frame_id: 1, info_matrix: [[1]], target_info_matrix: [[.8]] };
  const second = mergeTelemetryFrame(first, { episode_id: "a", frame_id: 2, info_matrix: [[.5]], target_info_matrix: [[.3]] });
  assert.equal(second.target_info_matrix[0][0], .3);
  const reset = mergeTelemetryFrame(second, { episode_id: "b", frame_id: 0 });
  assert.equal(reset.target_info_matrix, undefined);
});

test("map exposes keyboard cell inspection without depending on hover", async () => {
  const { default: CanvasMap } = await server.ssrLoadModule("/src/view/components/CanvasMap.jsx");
  const html = renderToStaticMarkup(createElement(CanvasMap, { frame: { info_matrix: [[0]], uavs: [], contacts: [] } }));
  assert.ok(html.includes('tabindex="0"'));
  assert.ok(html.includes('aria-label="栅格信息"'));
});

test("cell tooltip flips left without hiding the inspected right-edge cell", async () => {
  const { drawHoverTooltip } = await server.ssrLoadModule("/src/view/renderer/layers.js");
  const ctx = canvas();
  drawHoverTooltip(ctx, { col: 35, row: 10, I: .5, V: .8 }, 10, 390, 10, 800, 600);
  const [x, , width] = ctx.calls.fills[0].rect;
  assert.ok(x + width < 740);
});
