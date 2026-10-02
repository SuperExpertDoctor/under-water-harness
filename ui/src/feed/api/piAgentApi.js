import httpApi from "./httpApi";

/**
 * PI Agent 任务分配接口。
 *
 * The UI only submits/reads contracts. The PI Agent implementation remains
 * on the backend.
 */
export const piAgentApi = {
  getStatus(options) {
    return httpApi.get("/api/pi-agent/status", options);
  },
  assignTasks(payload, options) {
    return httpApi.post("/api/pi-agent/task-assignment", payload, options);
  },
  getAssignments(options) {
    return httpApi.get("/api/pi-agent/task-assignment", options);
  },
  cancelAssignment(assignmentId, payload = {}, options) {
    return httpApi.post(
      `/api/pi-agent/task-assignment/${encodeURIComponent(assignmentId)}/cancel`,
      payload,
      options,
    );
  },
};

export default piAgentApi;
