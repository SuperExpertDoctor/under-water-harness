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

test("pending approval sits inline in the linked conversation with scoped choices", async () => {
  const { default: Conversation } = await server.ssrLoadModule("/src/components/ConversationPanel.jsx");
  const mission = { ready: true, state: { messages: [{ id: "answer", role: "assistant", run_id: "run-7", text: "Planning" }], events: [
    { type: "tool_completed", data: { plan_id: "plan-7", run_id: "run-7" } },
  ], plans: [{ plan_id: "plan-7", kind: "search", members: ["UUV-1"], status: "pending_approval", reason: "human_required", risk: .62, expires_at_s: 200, fallback: "protective_pause" }] } };
  const html = renderToStaticMarkup(createElement(Conversation, { mission, frame: { sim_time_min: 1 }, readOnly: false }));
  assert.doesNotMatch(html, /aria-modal="true"|approval-blocker/);
  assert.match(html, /Planning[\s\S]*plan-7/);
  assert.match(html, /需要你的批准/);
  assert.match(html, /请求模式/);
  assert.match(html, /执行范围（含转场）/);
  assert.match(html, /plan-7/);
  assert.match(html, /仅允许本次/);
  assert.match(html, /本会话允许同类操作/);
  assert.match(html, /拒绝/);
  assert.match(html, /<\/article><section class="approval-request pending"/);
  assert.match(html, /<fieldset class="approval-options"/);
  assert.equal((html.match(/type="radio"/g) || []).length, 3);
  assert.equal((html.match(/class="approval-submit" type="submit"/g) || []).length, 1);
  assert.match(html, /等待审批后继续对话/);
  assert.doesNotMatch(html, /<textarea/);
  assert.doesNotMatch(html, /conversation-approvals/);
});

test("resolved approval remains in the linked turn without another decision button", async () => {
  const { default: Conversation } = await server.ssrLoadModule("/src/components/ConversationPanel.jsx");
  const mission = { ready: true, state: { messages: [{ id: "answer", role: "assistant", plan_id: "plan-9", text: "Done" }], events: [
    { type: "approval_requested", data: { plan_id: "plan-9" } },
  ], plans: [{ plan_id: "plan-9", kind: "track", members: ["UUV-2"], status: "rejected" }] } };
  const html = renderToStaticMarkup(createElement(Conversation, { mission, frame: { sim_time_min: 1 }, readOnly: false }));
  assert.match(html, /Done[\s\S]*已拒绝/);
  assert.doesNotMatch(html, /approve-command/);
});

test("unlinked approval names the missing association rather than guessing a turn", async () => {
  const { default: Conversation } = await server.ssrLoadModule("/src/components/ConversationPanel.jsx");
  const mission = { ready: true, state: { messages: [{ id: "other", role: "assistant", text: "Unrelated" }], plans: [
    { plan_id: "plan-unknown", kind: "search", members: ["UUV-1"], status: "pending_approval" },
  ] } };
  const html = renderToStaticMarkup(createElement(Conversation, { mission, frame: {}, readOnly: false }));
  assert.match(html, /未关联到对话回合/);
  assert.match(html, /plan-unknown/);
});

test("multiple pending requests remain visible inline", async () => {
  const { default: Conversation } = await server.ssrLoadModule("/src/components/ConversationPanel.jsx");
  const mission = { ready: true, state: { messages: [], plans: [
    { plan_id: "first", kind: "search", status: "pending_approval" },
    { plan_id: "second", kind: "track", status: "pending_approval" },
  ] } };
  const html = renderToStaticMarkup(createElement(Conversation, { mission, frame: {}, readOnly: false }));
  assert.match(html, /first/);
  assert.match(html, /second/);
});

test("pending approval does not block inspecting mission data", async () => {
  const { default: Sidebar } = await server.ssrLoadModule("/src/components/RightSidebar.jsx");
  const mission = { ready: true, state: { messages: [], plans: [{ plan_id: "pending", status: "pending_approval" }] } };
  const html = renderToStaticMarkup(createElement(Sidebar, { mission, frame: {}, readOnly: false }));
  assert.doesNotMatch(html, /aria-selected="false"[^>]*disabled=""[^>]*>数据/);
});

test("decision tab renders a time ordered scheduling table", async () => {
  const { default: Drawer } = await server.ssrLoadModule("/src/components/BottomDrawer.jsx");
  const frame = { episode_id: "e1", plans: [{ plan_id: "p1", kind: "search", status: "active", members: ["UUV-1"], decision_reason: "补充覆盖" }] };
  const events = [{ id: 1, episode_id: "e1", time: 2, type: "mission_assignment_committed", data: { plan_id: "p1" } }];
  const html = renderToStaticMarkup(createElement(Drawer, { frame, events, visible: true, readOnly: true }));
  assert.match(html, /<table class="decision-table"/);
  assert.match(html, /时间[\s\S]*决策\/调度[\s\S]*为什么[\s\S]*参与 UUV/);
  assert.match(html, /补充覆盖/);
  assert.match(html, /目标状态/);
  assert.doesNotMatch(html, /aria-label="参数"|aria-label="任务操作"|aria-label="AIS"/);
});

test("operations view lists approval history without duplicate decision actions", async () => {
  const { default: AgentPanel } = await server.ssrLoadModule("/src/components/AgentPanel.jsx");
  const mission = { state: { plans: [{ plan_id: "plan-history", kind: "search", members: ["UUV-1"], status: "pending_approval" }] } };
  const html = renderToStaticMarkup(createElement(AgentPanel, { tab: "approvals", mission, frame: {}, readOnly: false }));
  assert.match(html, /审批记录/);
  assert.match(html, /plan-history/);
  assert.doesNotMatch(html, /批准<\/button>|拒绝<\/button>/);
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

test("passive tracking draws a wide fixed fan in a distinct translucent color", async () => {
  const { drawUavScanRanges } = await server.ssrLoadModule("/src/renderer/layers.js");
  const fills = [];
  const arcs = [];
  const context = { save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, stroke() {}, setLineDash() {},
    fill() { fills.push(context.fillStyle); },
    arc(_x, _y, radius, from, to) { arcs.push({ radius, from, to }); } };
  drawUavScanRanges(context, [{ id: "T1", status: "tracking", position: [2, 3], heading_rad: 0,
    sensor_mode: "passive", sensor_roles: { side_scan: false, forward_active: true, forward_passive: true },
    sensor_radius_cells: 3.5, forward_active_half_angle_deg: 55, forward_passive_half_angle_deg: 150 }], 10, 0, 0, []);
  assert.equal(fills.length, 1);
  assert.match(fills[0], /52, 211, 153/);
  assert.doesNotMatch(fills[0], /225, 151, 55/);
  assert.equal(arcs.length, 1);
  assert.ok(Math.abs(arcs[0].to - arcs[0].from - Math.PI * 300 / 180) < 1e-9);
});

test("boats with sensors off draw no forward fan", async () => {
  const { drawUavScanRanges } = await server.ssrLoadModule("/src/renderer/layers.js");
  const fills = [];
  const context = { save() {}, restore() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, stroke() {}, setLineDash() {},
    fill() { fills.push(context.fillStyle); },
    arc() {} };
  drawUavScanRanges(context, [{ id: "X1", status: "transit", position: [2, 3], heading_rad: 0,
    sensor_mode: "off", sensor_roles: { side_scan: false, forward_active: true, forward_passive: false },
    sensor_radius_cells: 3.5 }], 10, 0, 0, []);
  assert.equal(fills.length, 0);
});
