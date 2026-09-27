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
