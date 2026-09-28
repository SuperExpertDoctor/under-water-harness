import assert from "node:assert/strict";
import test from "node:test";
import { mergeMissionState, mergeTelemetryFrame, mutationPayload, selectionToMeters, routeToCells } from "./missionState.js";
import { applyInformationField, createInformationField, createInformationFieldModel, updateInformationField } from "./informationField.js";
import { DEMO_FRAME } from "./demoFrame.js";
import * as missionState from "./missionState.js";

test("streamed messages merge by id without dropping prior messages or duplicating text", () => {
  const prior = { episode_id: "a", cursor: 1, messages: [{ id: "user", text: "search" }, { id: "answer", text: "Plan", status: "streaming" }] };
  const next = mergeMissionState(prior, { episode_id: "a", cursor: 2, messages: [{ id: "answer", text: "Plan ready", status: "completed" }] });
  assert.equal(next.messages.length, 2);
  assert.equal(next.messages[1].text, "Plan ready");
  assert.equal(next.messages[1].status, "completed");
});

test("annotations preserve source identity and reject quotes outside the selected message", () => {
  const message = { id: "m1", text: "Approve this search plan", plan_id: "p1" };
  assert.equal(typeof missionState.annotationPayload, "function");
  assert.deepEqual(missionState.annotationPayload(message, "this search"), { message_id: "m1", quote: "this search", plan_id: "p1" });
  assert.equal(missionState.annotationPayload(message, "another message"), null);
  assert.equal(missionState.annotationPayload({ id: "long", text: "a".repeat(2001) }, "a".repeat(2001)), null);
  assert.deepEqual(missionState.annotationPayload({ id: "m2", text: "搜索完成下一步追踪" }, "搜索完成\n下一步追踪"),
    { message_id: "m2", quote: "搜索完成\n下一步追踪", plan_id: null });
});

test("generation rollover never interpolates a replacement from the departed boat", () => {
  const previous = { id: "UUV-1", generation: 1, position: [39, 12] };
  const current = { id: "UUV-1", generation: 2, position: [0, 20] };
  assert.equal(typeof missionState.interpolateUuv, "function");
  assert.deepEqual(missionState.interpolateUuv(previous, current, 0.5), current);
  assert.deepEqual(missionState.interpolateUuv({ ...previous, generation: 2 }, current, 0.5).position, [19.5, 16]);
});

test("event filters use boat contact task and severity associations", () => {
  const events = [{ id: 1, type: "tracking_established", data: { members: ["UUV-1"], contact_id: "c1", plan_id: "p1" } }, { id: 2, type: "tool_failed", level: "error", data: { uav_id: "UUV-2" } }];
  assert.equal(typeof missionState.filterMissionEvents, "function");
  assert.deepEqual(missionState.filterMissionEvents(events, { uuv: "UUV-1", contact: "c1", task: "p1" }), [events[0]]);
  assert.deepEqual(missionState.filterMissionEvents(events, { level: "error" }), [events[1]]);
});

test("high frequency tool progress is grouped without merging approval decisions", () => {
  assert.equal(typeof missionState.coalesceMissionEvents, "function");
  const events = [
    { id: 1, type: "tool_execution_update", data: { tool_call_id: "t1", text: "A" } },
    { id: 2, type: "tool_execution_update", data: { tool_call_id: "t1", text: "B" } },
    { id: 3, type: "approval_decided", data: { plan_id: "p1" } },
    { id: 4, type: "approval_decided", data: { plan_id: "p1" } },
  ];
  const result = missionState.coalesceMissionEvents(events);
  assert.equal(result.length, 3);
  assert.equal(result[0].repeat_count, 2);
  assert.equal(result[0].data.text, "B");
  assert.equal(events[0].repeat_count, undefined);
});

test("decision traces use explicit run and plan links without guessing a trigger", () => {
  const events = [
    { id: 1, episode_id: "e1", type: "target_found", data: { contact_id: "c1" } },
    { id: 2, episode_id: "e1", type: "agent_queued", data: { run_id: "r1", source: "target_found" } },
    { id: 3, episode_id: "e1", type: "tool_completed", data: { run_id: "r1", plan_id: "p1" } },
    { id: 4, episode_id: "e1", type: "approval_requested", data: { plan_id: "p1", members: ["UUV-1"] } },
    { id: 5, episode_id: "e1", type: "approval_decided", data: { plan_id: "p1", status: "approved" } },
    { id: 6, episode_id: "e1", type: "mission_assignment_committed", data: { plan_id: "p1", members: ["UUV-1"] } },
    { id: 7, episode_id: "e2", type: "approval_decided", data: { plan_id: "p1", status: "rejected" } },
  ];
  const [trace] = missionState.buildDecisionTraces(events, [{ plan_id: "p1", members: ["UUV-1"], status: "active" }], "e1");
  assert.equal(trace.runId, "r1");
  assert.equal(trace.steps.trigger.event.id, 2);
  assert.equal(trace.steps.trigger.label, "目标发现触发运行");
  assert.equal(trace.steps.plan.event.id, 3);
  assert.equal(trace.steps.approval.event.id, 5);
  assert.equal(trace.steps.execution.event.id, 6);
  assert.equal(trace.contactId, null);
  assert.deepEqual(trace.members, ["UUV-1"]);
});

test("decision traces expose missing evidence and historical truncation", () => {
  const [trace] = missionState.buildDecisionTraces([
    { id: 8, episode_id: "e1", type: "approval_requested", data: { plan_id: "p2" } },
  ], [{ plan_id: "p2", status: "pending_approval" }], "e1");
  assert.equal(trace.steps.trigger.event, null);
  assert.equal(trace.steps.trigger.label, "未记录/无法关联");
  assert.equal(trace.steps.plan.event, null);
  assert.equal(trace.steps.approval.event.id, 8);
  assert.equal(trace.steps.execution.event, null);
  assert.deepEqual(missionState.buildDecisionTraces([], [], "e1"), []);
});

test("approval requests attach only to an explicitly linked assistant turn", () => {
  const messages = [
    { id: "user-1", role: "user", run_id: "run-1" },
    { id: "answer-1", role: "assistant", run_id: "run-1" },
    { id: "answer-2", role: "assistant", run_id: "run-1" },
    { id: "answer-other", role: "assistant", run_id: "run-2", plan_id: "plan-direct" },
  ];
  const events = [
    { type: "tool_completed", data: { plan_id: "plan-linked", run_id: "run-1" } },
    { type: "tool_completed", data: { plan_id: "plan-missing", run_id: "run-3" } },
    { type: "approval_requested", data: { plan_id: "plan-linked" } },
  ];
  const plans = ["plan-linked", "plan-direct", "plan-missing", "plan-unlinked"].map((plan_id) => ({ plan_id, status: "pending_approval" }));
  const grouped = missionState.groupApprovalsByMessage(messages, plans, events);
  assert.deepEqual(grouped.byMessage.get("answer-2").map((plan) => plan.plan_id), ["plan-linked"]);
  assert.deepEqual(grouped.byMessage.get("answer-other").map((plan) => plan.plan_id), ["plan-direct"]);
  assert.deepEqual(grouped.unlinked.map((plan) => plan.plan_id), ["plan-missing", "plan-unlinked"]);
});

test("approval history retains decisions only when a request was recorded", () => {
  const messages = [{ id: "answer", role: "assistant", run_id: "run-1" }];
  const plans = [{ plan_id: "approved", status: "active" }, { plan_id: "automatic", status: "active" }, { plan_id: "denied", status: "rejected" }];
  const events = [
    { type: "tool_completed", data: { plan_id: "approved", run_id: "run-1" } },
    { type: "approval_requested", data: { plan_id: "approved" } },
  ];
  const grouped = missionState.groupApprovalsByMessage(messages, plans, events);
  assert.deepEqual(grouped.byMessage.get("answer").map((plan) => plan.plan_id), ["approved"]);
  assert.deepEqual(grouped.unlinked.map((plan) => plan.plan_id), ["denied"]);
});

test("resolved approval stays anchored to the message before the request", () => {
  const messages = [
    { id: "before", role: "assistant", run_id: "r1" },
    { id: "after", role: "assistant", run_id: "r1" },
  ];
  const events = [
    { id: 1, type: "message_start", data: { message_id: "before", run_id: "r1" } },
    { id: 2, type: "approval_requested", data: { plan_id: "p1" } },
    { id: 3, type: "tool_completed", data: { plan_id: "p1", run_id: "r1" } },
    { id: 4, type: "approval_decided", data: { plan_id: "p1" } },
    { id: 5, type: "message_start", data: { message_id: "after", run_id: "r1" } },
  ];
  const result = missionState.groupApprovalsByMessage(messages, [{ plan_id: "p1", status: "active" }], events);
  assert.deepEqual(result.byMessage.get("before")?.map((plan) => plan.plan_id), ["p1"]);
  assert.equal(result.byMessage.get("after"), undefined);
});

test("manual planning links the recorded candidate by result id", () => {
  const [trace] = missionState.buildDecisionTraces([
    { id: 1, episode_id: "e1", type: "tool_result", data: { result_id: "result-1", status: "succeeded" } },
    { id: 2, episode_id: "e1", type: "approval_requested", data: { plan_id: "p1" } },
  ], [{ plan_id: "p1", result_id: "result-1", status: "pending_approval" }], "e1");
  assert.equal(trace.steps.plan.event.id, 1);
  assert.equal(trace.steps.trigger.event, null);
});

test("decision rows show mission time, exact public reason and participating boats", () => {
  const events = [
    { id: 1, episode_id: "e1", time: 2, type: "approval_requested", data: { plan_id: "p1", members: ["UUV-1"] } },
    { id: 2, episode_id: "e1", time: 3, type: "mission_assignment_committed", data: { plan_id: "p2", members: ["UUV-2"] } },
  ];
  const rows = missionState.buildDecisionRows(events, [
    { plan_id: "p1", kind: "search", members: ["UUV-1"], decision_reason: "补齐北部覆盖空白", status: "pending_approval" },
    { plan_id: "p2", kind: "track", members: ["UUV-2"], status: "active" },
  ], "e1");
  assert.deepEqual(rows.map((row) => row.planId), ["p2", "p1"]);
  assert.equal(rows[0].timeSeconds, 180);
  assert.equal(rows[0].reason, "未提供公开理由");
  assert.equal(rows[1].reason, "补齐北部覆盖空白");
  assert.deepEqual(rows[1].members, ["UUV-1"]);
  assert.equal(rows[1].action, "区域搜索");
  assert.deepEqual(missionState.buildDecisionRows(events, [], "local-demo"), []);
});

test("decision row time follows the latest recorded approval or dispatch change", () => {
  const events = [
    { id: 1, episode_id: "e1", time: 2, type: "approval_requested", data: { plan_id: "p1" } },
    { id: 2, episode_id: "e1", time: 5, type: "approval_decided", data: { plan_id: "p1", status: "approved" } },
    { id: 3, episode_id: "e1", time: 5, type: "mission_assignment_committed", data: { plan_id: "p1" } },
    { id: 4, episode_id: "e1", time: 5.2, type: "tool_completed", data: { plan_id: "p1" } },
  ];
  const [row] = missionState.buildDecisionRows(events, [{ plan_id: "p1", kind: "search", status: "active" }], "e1");
  assert.equal(row.timeSeconds, 300);
  assert.equal(row.event.id, 3);
});

test("coverage descriptions distinguish historical coverage and window freshness", () => {
  const frame = { searchable_cells: 100, mission_metrics: { coverage_pct: 100, recent_coverage_pct: 32, coverage_window_min: 30 }, coverage_metrics: { fixed_searchable_area_km2: 1 } };
  const descriptions = missionState.coverageDescriptions(frame);
  assert.match(descriptions.coverage_pct, /历史/);
  assert.match(descriptions.recent_coverage_pct, /30 分钟/);
  assert.match(descriptions.recent_coverage_pct, /100/);
  assert.match(descriptions.recent_coverage_pct, /1 km²/);
});

test("reconnect deduplicates events and ignores older cursors", () => {
  const current = { episode_id: "a", cursor: 4, messages: [{ id: "m" }], events: [{ id: 4 }] };
  const next = mergeMissionState(current, { episode_id: "a", cursor: 5, events: [{ id: 4 }, { id: 5 }] });
  assert.deepEqual(next.events, [{ id: 4 }, { id: 5 }]);
  assert.equal(mergeMissionState(next, { episode_id: "a", cursor: 3, messages: [] }), next);
});

test("new episode drops messages, plans and telemetry matrices", () => {
  const next = mergeMissionState({ episode_id: "a", messages: [1], plans: [1], events: [{ id: 9 }] }, { episode_id: "b", cursor: 1, events: [{ id: 1 }] });
  assert.deepEqual(next.messages, []);
  assert.deepEqual(next.plans, []);
  assert.deepEqual(next.events, [{ id: 1 }]);
  assert.deepEqual(mergeTelemetryFrame({ episode_id: "a", frame_id: 10, info_matrix: [[1]] }, { episode_id: "b", frame_id: 1 }), { episode_id: "b", frame_id: 1 });
});

test("same episode telemetry retains omitted matrices and rejects older frame", () => {
  const frame = { episode_id: "a", frame_id: 10, info_matrix: [[1]] };
  assert.equal(mergeTelemetryFrame(frame, { episode_id: "a", frame_id: 9 }), frame);
  assert.deepEqual(mergeTelemetryFrame(frame, { episode_id: "a", frame_id: 11 }).info_matrix, [[1]]);
});

test("commands require live authenticated episode and cannot override episode", () => {
  assert.throws(() => mutationPayload("a", true, { text: "x" }), /readonly/);
  assert.throws(() => mutationPayload(null, false, {}), /episode/);
  assert.throws(() => mutationPayload("local-demo", false, {}), /episode/);
  assert.deepEqual(mutationPayload("a", false, { episode_id: "stale", text: "x" }), { episode_id: "a", text: "x" });
});

test("map selection converts downward grid coordinates to upward SI meters", () => {
  const area = { width_km: 4, height_km: 4, cell_size_km: .1 };
  assert.deepEqual(selectionToMeters([2, 3, 8, 9], area), [200, 3100, 800, 3700]);
  assert.deepEqual(routeToCells([[250, 3750, 0]], area), [[2, 2]]);
});

test("backend information is authoritative even beside actively scanning boats", () => {
  const frame = { episode_id: "a", information_source: "backend", info_matrix: [[.2]], value_matrix: [[.1]], sim_time_min: 10, uavs: [{ position: [0, 0], status: "searching" }] };
  const field = updateInformationField(createInformationField(), frame, 1, createInformationFieldModel());
  assert.deepEqual(field.info_matrix, [[.2]]);
  assert.equal(field.calculation_mode, "backend");
  assert.deepEqual(applyInformationField(frame, { info_matrix: [[1]] }).info_matrix, [[.2]]);
});

test("offline formation contains exactly eight UUVs", () => {
  assert.equal(DEMO_FRAME.uavs.length, 8);
});
