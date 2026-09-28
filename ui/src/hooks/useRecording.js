import { useCallback, useEffect, useRef, useState } from "react";

export async function recordingRequest(action, fetcher = fetch) {
  if (action !== "start" && action !== "stop") throw new Error("invalid_recording_action");
  const response = await fetcher(`/api/recording/${action}`, { method: "POST" });
  let result;
  try { result = await response.json(); }
  catch { throw new Error(response.ok ? "录制响应格式错误" : `录制服务不可用 (${response.status})`); }
  if (!response.ok) throw new Error(result.message || result.error_code || `recording_${response.status}`);
  return result;
}

export function createRecordingRequestTracker() {
  let latest = 0;
  let mutating = false;
  return {
    beginRead: () => ++latest,
    beginMutation: () => { mutating = true; latest += 1; },
    endMutation: () => { mutating = false; latest += 1; },
    acceptsRead: (request) => !mutating && request === latest,
  };
}

export function recordingPresentation(recording, error = null) {
  const seconds = Math.max(0, Math.floor(recording.elapsed_s || 0));
  const duration = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  let view;
  switch (recording.status) {
    case "starting": view = { label: "正在启动录制", action: null }; break;
    case "recording": view = { label: `录制中 ${duration}`, action: "stop" }; break;
    case "finalizing": view = { label: "正在保存视频", action: null }; break;
    case "completed": view = { label: `已保存 ${recording.filename}`, action: "start" }; break;
    case "failed": view = { label: `录制失败: ${recording.error}`, action: "start" }; break;
    default: view = { label: "", action: "start" };
  }
  if (!error || (error.source === "action" && error.atStatus !== recording.status)) return view;
  return { ...view, label: `${error.source === "status" ? "录制状态不可用" : "录制操作失败"}: ${error.message}`,
    action: error.source === "status" ? null : view.action, alert: true };
}

export default function useRecording() {
  const [status, setStatus] = useState({ status: "idle" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const tracker = useRef(createRecordingRequestTracker());

  useEffect(() => {
    let disposed = false;
    const refresh = async () => {
      const request = tracker.current.beginRead();
      try {
        const response = await fetch("/api/recording");
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        if (!disposed && tracker.current.acceptsRead(request)) {
          setStatus(result);
          setError((previous) => previous?.source === "status" ? null : previous);
        }
      } catch (failure) {
        if (!disposed && tracker.current.acceptsRead(request)) setError({ source: "status", message: failure.message });
      }
    };
    refresh();
    const timer = window.setInterval(refresh, 1500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, []);

  const act = useCallback(async (action) => {
    if (busy) return;
    tracker.current.beginMutation();
    setBusy(true);
    setError(null);
    try {
      setStatus(await recordingRequest(action));
    } catch (failure) {
      setError({ source: "action", atStatus: status.status, message: failure.message });
    } finally { tracker.current.endMutation(); setBusy(false); }
  }, [busy, status.status]);

  return { status, busy, error, start: () => act("start"), stop: () => act("stop") };
}
