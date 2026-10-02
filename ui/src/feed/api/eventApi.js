import httpApi from "./httpApi";

/**
 * 事件接口
 *
 * Backend contract:
 *   GET  /api/events
 *   POST /api/test/target-detected
 *   POST /api/test/target-lost
 */
export const eventApi = {
  list(options) {
    return httpApi.get("/api/events", options);
  },
  targetDetected(payload = {}, options) {
    return httpApi.post("/api/test/target-detected", payload, options);
  },
  targetLost(payload = {}, options) {
    return httpApi.post("/api/test/target-lost", payload, options);
  },
};

export default eventApi;
