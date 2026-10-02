import { useEffect, useRef, useState } from "react";
import { connectTelemetryStream } from "../api/websocketApi";
import { mergeTelemetryFrame } from "../../state/missionState";
import {
  createInformationField,
  createInformationFieldModel,
  updateInformationField,
} from "../../state/informationField";

const TELEMETRY_RENDER_INTERVAL_MS = 1000 / 60;

export default function useWebSocket(enabled) {
  const [frame, setFrame] = useState(null);
  const [status, setStatus] = useState("idle");
  const [information, setInformation] = useState(createInformationField);
  const pendingFrame = useRef(null);
  const pendingInformation = useRef(null);
  const publishFrame = useRef(null);
  const publishTimer = useRef(null);
  const lastPublishedAt = useRef(0);
  const latestFrame = useRef(null);
  const latestInformation = useRef(createInformationField());
  const informationModel = useRef(createInformationFieldModel());

  useEffect(() => {
    if (!enabled) {
      setStatus("idle");
      setFrame(null);
      setInformation(createInformationField());
      return undefined;
    }
    let disposed = false;

    const scheduleFramePublish = () => {
      if (publishFrame.current != null || publishTimer.current != null) return;
      publishFrame.current = window.requestAnimationFrame(() => {
        publishFrame.current = null;
        const elapsed = performance.now() - lastPublishedAt.current;
        if (elapsed < TELEMETRY_RENDER_INTERVAL_MS) {
          publishTimer.current = window.setTimeout(() => {
            publishTimer.current = null;
            scheduleFramePublish();
          }, TELEMETRY_RENDER_INTERVAL_MS - elapsed);
          return;
        }
        lastPublishedAt.current = performance.now();
        if (pendingFrame.current) {
          latestFrame.current = pendingFrame.current;
          setFrame(pendingFrame.current);
        }
        if (pendingInformation.current) {
          latestInformation.current = pendingInformation.current;
          setInformation(pendingInformation.current);
        }
      });
    };

    const stop = connectTelemetryStream({
      onStatus: (nextStatus) => {
        if (!disposed) setStatus(nextStatus);
      },
      onError: () => {
        if (!disposed) setStatus("error");
      },
      onFrame: (next) => {
        if (next?.frame_id == null || disposed) return;
        const previous = pendingFrame.current || latestFrame.current;
        if (previous?.episode_id !== next.episode_id) {
          latestInformation.current = createInformationField();
          informationModel.current = createInformationFieldModel();
        }
        const nextInformation = updateInformationField(
          latestInformation.current,
          next,
          Date.now(),
          informationModel.current,
        );
        latestInformation.current = nextInformation;
        pendingInformation.current = nextInformation;
        // WebSocket delivery is asynchronous and can burst when the
        // simulator is faster than the display. Conflate snapshots and
        // publish at a 60 Hz animation cadence instead of scheduling an
        // unbounded React render queue.
        // Matrix deltas are omitted from most live frames. Preserve the
        // last known matrices locally so canvas rendering and inspection
        // keep a complete view without retransmitting them every time.
        pendingFrame.current = mergeTelemetryFrame(previous, next);
        scheduleFramePublish();
      },
    });

    return () => {
      disposed = true;
      stop();
      if (publishFrame.current != null) window.cancelAnimationFrame(publishFrame.current);
      if (publishTimer.current != null) window.clearTimeout(publishTimer.current);
      publishFrame.current = null;
      publishTimer.current = null;
      pendingFrame.current = null;
      pendingInformation.current = null;
      lastPublishedAt.current = 0;
      latestFrame.current = null;
      latestInformation.current = createInformationField();
      informationModel.current = createInformationFieldModel();
    };
  }, [enabled]);

  return { frame, status, information };
}
