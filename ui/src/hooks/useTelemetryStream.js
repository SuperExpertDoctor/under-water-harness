import { useEffect, useRef, useState } from "react";
import { connectTelemetryStream } from "../api/websocketApi";

const FRAME_INTERVAL_MS = 1000 / 60;

export default function useTelemetryStream(enabled = true) {
  const [frame, setFrame] = useState(null);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState(null);
  const pendingFrame = useRef(null);
  const latestFrame = useRef(null);
  const publishTimer = useRef(null);
  const lastPublishedAt = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setStatus("idle");
      return undefined;
    }

    const publish = () => {
      const elapsed = performance.now() - lastPublishedAt.current;
      if (elapsed < FRAME_INTERVAL_MS) {
        publishTimer.current = window.setTimeout(publish, FRAME_INTERVAL_MS - elapsed);
        return;
      }
      publishTimer.current = null;
      lastPublishedAt.current = performance.now();
      if (!pendingFrame.current) return;
      latestFrame.current = pendingFrame.current;
      setFrame(pendingFrame.current);
      pendingFrame.current = null;
    };

    const stop = connectTelemetryStream({
      onStatus: setStatus,
      onError: setError,
      onFrame: (nextFrame) => {
        pendingFrame.current = {
          ...(latestFrame.current || {}),
          ...nextFrame,
        };
        if (publishTimer.current == null) publish();
      },
    });

    return () => {
      stop();
      if (publishTimer.current) window.clearTimeout(publishTimer.current);
      publishTimer.current = null;
      pendingFrame.current = null;
      latestFrame.current = null;
    };
  }, [enabled]);

  return { frame, status, error };
}
