import httpApi from "./httpApi";

/**
 * Algorithm boundary.
 *
 * These functions keep the UI independent from the task allocator/LLM
 * implementation. Replace the endpoint paths with the backend contract when
 * the algorithm service is connected; the UI components do not change.
 */
export const algorithmApi = {
  getStatus() {
    return httpApi.get("/api/algorithm/status");
  },
  requestTaskPlan(payload) {
    return httpApi.post("/api/algorithm/task-plan", payload);
  },
  requestDecision(payload) {
    return httpApi.post("/api/algorithm/decision", payload);
  },
  sendCommand(payload) {
    return httpApi.post("/api/algorithm/commands", payload);
  },
};

export default algorithmApi;
