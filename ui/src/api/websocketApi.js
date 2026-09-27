const DEFAULT_PATH = "/ws/live";

function websocketUrl(path = DEFAULT_PATH) {
  const configuredUrl = import.meta.env.VITE_WS_URL;
  if (configuredUrl) return configuredUrl;
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}${path}`;
}

/**
 * Subscribe to a JSON WebSocket stream.
 *
 * Payloads are intentionally passed through without reshaping. The callback
 * receives the complete object sent by the backend.
 */
export function connectJsonStream({
  path = DEFAULT_PATH,
  onMessage,
  onStatus,
  onError,
  heartbeatMs = 25_000,
} = {}) {
  let socket;
  let heartbeat;
  let closedByCaller = false;
  let reconnectTimer;
  let retry = 0;

  const connect = () => {
    if (closedByCaller) return;
    onStatus?.(retry ? "reconnecting" : "connecting");
    socket = new WebSocket(websocketUrl(path));

    socket.onopen = () => {
      retry = 0;
      onStatus?.("connected");
      heartbeat = window.setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) socket.send("ping");
      }, heartbeatMs);
    };

    socket.onmessage = (event) => {
      if (event.data === "pong") return;
      try {
        onMessage?.(JSON.parse(event.data));
      } catch (error) {
        onError?.(error);
        onStatus?.("error");
      }
    };

    socket.onerror = () => {
      onStatus?.("error");
      socket?.close();
    };

    socket.onclose = () => {
      if (heartbeat) window.clearInterval(heartbeat);
      if (closedByCaller) return;
      onStatus?.("reconnecting");
      retry += 1;
      reconnectTimer = window.setTimeout(connect, Math.min(30_000, 1000 * 2 ** Math.min(retry, 5)));
    };
  };

  connect();
  return () => {
    closedByCaller = true;
    if (heartbeat) window.clearInterval(heartbeat);
    if (reconnectTimer) window.clearTimeout(reconnectTimer);
    socket?.close();
  };
}

/**
 * Original mission-frame/v2 stream. Kept for the existing simulator adapter.
 */
export function connectTelemetryStream(options = {}) {
  return connectJsonStream({
    path: options.path || import.meta.env.VITE_TELEMETRY_WS_PATH || "/ws/live",
    ...options,
    onMessage: (frame) => {
      if (frame?.frame_id != null) options.onFrame?.(frame);
    },
  });
}

/**
 * New state stream reserved by the backend contract:
 * WebSocket /ws/state
 */
export function connectStateStream(options = {}) {
  return connectJsonStream({
    path: options.path || import.meta.env.VITE_STATE_WS_PATH || "/ws/state",
    ...options,
    onMessage: (state) => options.onState?.(state),
  });
}

export default connectTelemetryStream;
