import httpApi from "./httpApi";

/**
 * 任务装配接口。
 *
 * The assembled task is returned by the backend in its own schema. The UI
 * does not build task routes or modify allocator decisions.
 */
export const taskAssemblyApi = {
  getTasks(options) {
    return httpApi.get("/api/task-assembly/tasks", options);
  },
  assemble(payload, options) {
    return httpApi.post("/api/task-assembly/assemble", payload, options);
  },
  getTask(taskId, options) {
    return httpApi.get(`/api/task-assembly/tasks/${encodeURIComponent(taskId)}`, options);
  },
  updateTask(taskId, payload, options) {
    return httpApi.patch(
      `/api/task-assembly/tasks/${encodeURIComponent(taskId)}`,
      payload,
      options,
    );
  },
};

export default taskAssemblyApi;
