import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

const server = await createServer({ root: fileURLToPath(new URL("../../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());

test("status data distinguishes side scan, front active and front passive", async () => {
  const { default: Panel } = await server.ssrLoadModule("/src/components/UuvStatusPanel.jsx");
  const html = renderToStaticMarkup(createElement(Panel, { frame: { uavs: [
    { id: "UUV-1", generation: 1, status: "searching", sensor_mode: "active", sensor_roles: { side_scan: true, forward_active: true, forward_passive: false } },
    { id: "UUV-2", generation: 1, status: "acquiring", sensor_mode: "active", sensor_roles: { side_scan: false, forward_active: true, forward_passive: false } },
    { id: "UUV-3", generation: 1, status: "tracking", sensor_mode: "passive", sensor_roles: { side_scan: false, forward_active: false, forward_passive: true } },
  ] } }));
  assert.match(html, /侧扫搜索/);
  assert.match(html, /前置主动捕获/);
  assert.match(html, /前置被动跟踪/);
});

test("configured primary rolling window is visible and selectable", async () => {
  const { default: Coverage } = await server.ssrLoadModule("/src/components/CoveragePanel.jsx");
  const html = renderToStaticMarkup(createElement(Coverage, { frame: { episode_id: "m", coverage_metrics: {
    schema_version: "persistent-coverage/v1", status: "ok", as_of_min: 5,
    primary_window_min: 2, fixed_searchable_area_km2: 16, cumulative_pct: 50,
    windows: [{ minutes: 2, coverage_pct: 25, covered_area_km2: 4, window_complete: true }],
  } } }));
  assert.match(html, /最近 2 分钟/);
  assert.match(html, /25\.00%/);
});

test("pending approval appears inline without opening a details accordion", async () => {
  const { default: Conversation } = await server.ssrLoadModule("/src/components/ConversationPanel.jsx");
  const mission = { ready: true, state: { messages: [], plans: [{ plan_id: "plan-7", kind: "search", members: ["UUV-1"], status: "pending_approval", risk: .62, expires_at_s: 200 }] } };
  const html = renderToStaticMarkup(createElement(Conversation, { mission, frame: { sim_time_min: 1 }, readOnly: false }));
  assert.match(html, /待批准计划/);
  assert.match(html, /plan-7/);
  assert.match(html, /批准/);
  assert.match(html, /拒绝/);
});

test("map renders two rotated side-scan swaths instead of an omnidirectional disk", async () => {
  const { drawUavScanRanges } = await server.ssrLoadModule("/src/renderer/layers.js");
  const arcs = [];
  const context = { save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() {}, stroke() {}, setLineDash() {},
    arc(_x, _y, radius, from, to) { arcs.push({ radius, from, to }); } };
  drawUavScanRanges(context, [{ id: "U1", status: "searching", position: [2, 3], heading_rad: 0,
    sensor_mode: "active", sensor_roles: { side_scan: true, forward_active: false },
    sensor_radius_cells: 3.5, side_scan_inner_radius_cells: .5, side_scan_half_angle_deg: 38 }], 10, 0, 0, []);
  assert.equal(arcs.filter((arc) => arc.radius === 35).length, 2);
  assert.equal(arcs.filter((arc) => arc.radius === 5).length, 2);
  assert.ok(arcs.some((arc) => arc.from > 0 && arc.from < Math.PI));
  assert.ok(arcs.some((arc) => arc.from < 0));
});
