import assert from "node:assert/strict";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";

const server = await createServer({ root: fileURLToPath(new URL("../", import.meta.url)), server: { middlewareMode: true, hmr: false, ws: false }, appType: "custom" });
test.after(() => server.close());

test("console exposes timeline agent metrics and retained secondary views", async () => {
  const { default: Drawer } = await server.ssrLoadModule("/view/components/BottomDrawer.jsx");
  const html = renderToStaticMarkup(createElement(Drawer, { frame: {}, visible: true }));
  for (const label of ["时间线", "Agent 运行", "任务指标", "区域", "目标状态"]) assert.ok(html.includes(label), label);
  assert.ok(!html.includes('aria-label="参数"'));
  assert.ok(!html.includes('aria-label="任务操作"'));
});

test("contact transitions do not masquerade as completed decisions", async () => {
  const { default: Drawer } = await server.ssrLoadModule("/view/components/BottomDrawer.jsx");
  const events = [
    { id: 1, type: "provisional_contact_aborted", time: 1, data: { uuv_id: "UUV-1" } },
    { id: 2, type: "tracking_reacquisition_started", time: 2, data: { uuv_id: "UUV-1" } },
    { id: 3, type: "stale_contact_search_resumed", time: 3, data: { members: ["UUV-1", "UUV-2"] } },
  ];
  const html = renderToStaticMarkup(createElement(Drawer, { frame: {}, events, visible: true }));
  for (const label of ["临时接触中止", "主动重新捕获", "失联归还搜索"]) assert.ok(!html.includes(label), label);
});

test("conversation renders safe Markdown without raw HTML or executable links", async () => {
  const { default: Conversation } = await server.ssrLoadModule("/view/components/ConversationPanel.jsx");
  const mission = { ready: true, state: { messages: [{ id: "m", role: "assistant", text: "**Observed** <script>alert(1)</script> [bad](javascript:alert(1))", status: "streaming" }] } };
  const html = renderToStaticMarkup(createElement(Conversation, { mission, frame: {}, readOnly: false }));
  assert.ok(html.includes("<strong>Observed</strong>"));
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes('href="javascript:'));
  assert.ok(html.includes("下一回合跟进"));
});

test("boat data includes generation energy range heading speed and sensor phase", async () => {
  const { default: Panel } = await server.ssrLoadModule("/view/components/UuvStatusPanel.jsx");
  const frame = { uavs: [{ id: "UUV-1", generation: 3, energy_pct: 42, remaining_range_m: 4800, speed_mps: 4, heading_deg: 90, sensor_mode: "passive", operation_mode: "track", task_phase: "acquire", position: [1, 2] }] };
  const html = renderToStaticMarkup(createElement(Panel, { frame, selectedUuvId: "UUV-1" }));
  for (const label of ["G3", "42.0%", "4800 m", "4.0 m/s", "90.0°", "passive", "建立协同观测"]) assert.ok(html.includes(label), label);
});

test("conversation exposes native tool results in collapsed traceable output", async () => {
  const { default: Conversation } = await server.ssrLoadModule("/view/components/ConversationPanel.jsx");
  const mission = { ready: true, state: { messages: [{ id: "m", role: "assistant", text: "Plan ready", run_id: "r1" }], events: [{ id: 3, type: "tool_execution_end", data: { run_id: "r1", tool_call_id: "call-1", tool_name: "plan_search", text: "candidate-42", is_error: false } }] } };
  const html = renderToStaticMarkup(createElement(Conversation, { mission, frame: {}, readOnly: false }));
  assert.match(html, /<details class="tool-output"/);
  assert.ok(html.includes("plan_search"));
  assert.ok(html.includes("candidate-42"));
});

test("read-only replay does not expose messages or active generation from the live episode", async () => {
  const { default: Conversation } = await server.ssrLoadModule("/view/components/ConversationPanel.jsx");
  const mission = { ready: true, state: { messages: [{ id: "live-only", role: "assistant", text: "LIVE SECRET TASK" }], jobs: [{ run_id: "live-run", status: "running" }] } };
  const html = renderToStaticMarkup(createElement(Conversation, { mission, frame: { episode_id: "replay" }, readOnly: true }));
  assert.ok(!html.includes("LIVE SECRET TASK"));
  assert.ok(!html.includes("live-run"));
  assert.ok(html.includes("回放只读"));
});

test("map draws only active sensor coverage at the backend radius", async () => {
  const { drawUavScanRanges } = await server.ssrLoadModule("/view/renderer/layers.js");
  const radii = [];
  const context = { save() {}, restore() {}, beginPath() {}, fill() {}, stroke() {}, setLineDash() {}, arc(_x, _y, radius) { radii.push(radius); } };
  drawUavScanRanges(context, [
    { id: "UUV-1", position: [2, 3], status: "searching", sensor_mode: "active", sensor_radius_cells: 3.5 },
    { id: "UUV-2", position: [4, 5], status: "tracking", sensor_mode: "passive", sensor_radius_cells: 3.5 },
  ], 10, 0, 0, []);
  assert.deepEqual(radii, [35]);
});

test("map heading rotates the fallback hull from SI counterclockwise to screen coordinates", async () => {
  const { drawUavs } = await server.ssrLoadModule("/view/renderer/layers.js");
  const rotations = [];
  const context = { save() {}, restore() {}, translate() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() {}, rotate(angle) { rotations.push(angle); } };
  drawUavs(context, [{ id: "UUV-1", position: [2, 3], heading_deg: 90, status: "searching" }], 10, 0, 0, null, {}, []);
  assert.deepEqual(rotations, [-Math.PI / 2]);
});

test("top-row region labels leave clearance for the task-area header", async () => {
  const { drawLabels } = await server.ssrLoadModule("/view/renderer/layers.js");
  const rectangles = [];
  const context = { save() {}, restore() {}, strokeRect() {}, measureText: () => ({ width: 30 }), fillText() {}, fillRect(...rect) { rectangles.push(rect); } };
  const placed = drawLabels(context, { search_regions: [{ id: "r1", assigned_uav_id: "UUV-1", cells: [[0, 0]], bbox: [0, 0, 10, 20] }] },
    10, 0, 0, { x: 0, y: 0, width: 400, height: 400 }, null, null, null, []);
  assert.ok(placed.every((label) => label.hidden || label.y >= 22));
  assert.ok(rectangles.every((rect) => rect[1] >= 22));
});
