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

export async function exportRecordingFile(fetcher = fetch) {
  const response = await fetcher("/api/recording/download");
  if (!response.ok) {
    let message = `录制导出不可用 (${response.status})`;
    try {
      const body = await response.json();
      message = body.message || body.error_code || message;
    } catch { /* keep the status-derived message */ }
    throw new Error(message);
  }
  return response.blob();
}

export async function pickRecordingTarget(picker = window.showSaveFilePicker?.bind(window)) {
  if (!picker) return null;
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  return picker({
    suggestedName: `mission-${stamp}.mp4`,
    types: [{ description: "MP4 视频", accept: { "video/mp4": [".mp4"] } }],
  });
}

export async function saveRecordingBlob(blob, filename, handle = null) {
  if (handle) {
    const writable = await handle.createWritable();
    await writable.write(blob);
    await writable.close();
    return "picker";
  }
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
  return "download";
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
  const exportPending = useRef(false);
  const exportHandle = useRef(null);
  const picking = useRef(false);

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

  useEffect(() => {
    if (!exportPending.current || (status.status !== "completed" && status.status !== "failed")) return;
    exportPending.current = false;
    const handle = exportHandle.current;
    exportHandle.current = null;
    if (status.status !== "completed" || !status.filename) return;
    (async () => {
      try {
        await saveRecordingBlob(await exportRecordingFile(), status.filename, handle);
      } catch (failure) {
        setError({ source: "action", atStatus: "completed", message: failure.message });
      }
    })();
  }, [status]);

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

  // The save dialog must open inside the click gesture: transient activation
  // expires long before encoding finishes. Cancelling it skips the export;
  // the finished MP4 stays on the server either way.
  const stop = useCallback(async () => {
    if (picking.current) return;
    picking.current = true;
    exportPending.current = true;
    try {
      exportHandle.current = await pickRecordingTarget();
    } catch (failure) {
      exportHandle.current = null;
      if (failure?.name === "AbortError") {
        exportPending.current = false;
      } else {
        setError({ source: "action", atStatus: status.status, message: failure.message });
      }
    }
    picking.current = false;
    act("stop");
  }, [act, status.status]);

  return { status, busy, error, start: () => act("start"), stop };
}
