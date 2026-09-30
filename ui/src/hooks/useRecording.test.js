import assert from "node:assert/strict";
import test from "node:test";
import { createRecordingRequestTracker, exportRecordingFile, pickRecordingTarget, recordingRequest, recordingPresentation, saveRecordingBlob } from "./useRecording.js";

test("recording actions send no caller-controlled URL or output path", async () => {
  const calls = [];
  const fetcher = async (...args) => {
    calls.push(args);
    return { ok: true, json: async () => ({ status: args[0].endsWith("stop") ? "finalizing" : "recording" }) };
  };
  assert.equal((await recordingRequest("start", fetcher)).status, "recording");
  assert.equal((await recordingRequest("stop", fetcher)).status, "finalizing");
  assert.deepEqual(calls.map(([path]) => path), ["/api/recording/start", "/api/recording/stop"]);
  assert.ok(calls.every(([, options]) => options.method === "POST" && !options.body));
});

test("recording presentation distinguishes active, encoding, saved and failed states", () => {
  assert.equal(recordingPresentation({ status: "recording", elapsed_s: 75 }).label, "录制中 01:15");
  assert.equal(recordingPresentation({ status: "recording" }).action, "stop");
  assert.equal(recordingPresentation({ status: "finalizing" }).label, "正在保存视频");
  assert.equal(recordingPresentation({ status: "finalizing" }).action, null);
  assert.equal(recordingPresentation({ status: "completed", filename: "mission.mp4" }).label, "已保存 mission.mp4");
  assert.equal(recordingPresentation({ status: "failed", error: "encoder failed" }).label, "录制失败: encoder failed");
});

test("late or overlapping status reads cannot overwrite a completed recording mutation", () => {
  const tracker = createRecordingRequestTracker();
  const before = tracker.beginRead();
  tracker.beginMutation();
  const during = tracker.beginRead();
  assert.equal(tracker.acceptsRead(before), false);
  assert.equal(tracker.acceptsRead(during), false);
  tracker.endMutation();
  assert.equal(tracker.acceptsRead(during), false);
  const older = tracker.beginRead();
  const latest = tracker.beginRead();
  assert.equal(tracker.acceptsRead(older), false);
  assert.equal(tracker.acceptsRead(latest), true);
});

test("non-JSON HTTP errors remain useful recording errors", async () => {
  await assert.rejects(recordingRequest("start", async () => ({ ok: false, status: 500, json: async () => { throw new SyntaxError("Unexpected token"); } })), /录制服务不可用.*500/);
});

test("export fetches the finished mp4 and surfaces server errors", async () => {
  const blob = { size: 13 };
  assert.equal(await exportRecordingFile(async () => ({ ok: true, blob: async () => blob })), blob);
  await assert.rejects(
    exportRecordingFile(async () => ({ ok: false, status: 409, json: async () => ({ error_code: "recording_not_ready" }) })),
    /recording_not_ready/);
  await assert.rejects(
    exportRecordingFile(async () => ({ ok: false, status: 500, json: async () => { throw new SyntaxError("Unexpected token"); } })),
    /录制导出不可用.*500/);
});

test("save target picker asks for an mp4 path", async () => {
  const options = [];
  const handle = { name: "chosen" };
  assert.equal(await pickRecordingTarget(async (pick) => {
    options.push(pick);
    return handle;
  }), handle);
  assert.match(options[0].suggestedName, /^mission-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}\.mp4$/);
  assert.equal(options[0].types[0].accept["video/mp4"][0], ".mp4");
  assert.equal(await pickRecordingTarget(null), null);
});

test("finished mp4 writes into the chosen file handle", async () => {
  const writes = [];
  const handle = {
    createWritable: async () => ({
      write: async (data) => writes.push(data),
      close: async () => writes.push("closed"),
    }),
  };
  const result = await saveRecordingBlob("video-bytes", "mission-1.mp4", handle);
  assert.equal(result, "picker");
  assert.deepEqual(writes, ["video-bytes", "closed"]);
});

test("fresh server state replaces stale operation errors without hiding a current failure", () => {
  const conflict = { source: "action", atStatus: "idle", message: "recording_active" };
  assert.equal(recordingPresentation({ status: "idle" }, conflict).label, "录制操作失败: recording_active");
  assert.equal(recordingPresentation({ status: "recording", elapsed_s: 5 }, conflict).label, "录制中 00:05");
  const unavailable = recordingPresentation({ status: "recording" }, { source: "status", message: "HTTP 503" });
  assert.equal(unavailable.label, "录制状态不可用: HTTP 503");
  assert.equal(unavailable.action, null);
});
