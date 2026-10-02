import httpApi from "./httpApi";

/**
 * 仿真状态接口
 *
 * Backend contract:
 *   GET  /api/state
 *   POST /api/simulation/start
 *   POST /api/simulation/pause
 *   POST /api/simulation/reset
 */
export const stateApi = {
  getState(options) {
    return httpApi.get("/api/state", options);
  },
  startSimulation(payload = {}, options) {
    return httpApi.post("/api/simulation/start", payload, options);
  },
  pauseSimulation(payload = {}, options) {
    return httpApi.post("/api/simulation/pause", payload, options);
  },
  resetSimulation(payload = {}, options) {
    return httpApi.post("/api/simulation/reset", payload, options);
  },
};

export default stateApi;
