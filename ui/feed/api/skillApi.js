import httpApi from "./httpApi";

/**
 * Skill 统一接口。
 *
 * Backend contract is intentionally centralized here so the UI can call
 * skills without knowing whether a skill is implemented by Python, an agent,
 * or another service.
 */
export const skillApi = {
  list(options) {
    return httpApi.get("/api/skills", options);
  },
  get(skillId, options) {
    return httpApi.get(`/api/skills/${encodeURIComponent(skillId)}`, options);
  },
  save(skillId, payload = {}, options) {
    return httpApi.put(`/api/skills/${encodeURIComponent(skillId)}`, payload, options);
  },
  execute(skillId, payload = {}, options) {
    return httpApi.post(
      `/api/skills/${encodeURIComponent(skillId)}/execute`,
      payload,
      options,
    );
  },
  cancelRun(runId, payload = {}, options) {
    return httpApi.post(
      `/api/skills/runs/${encodeURIComponent(runId)}/cancel`,
      payload,
      options,
    );
  },
  setConfig(payload = {}, options) {
    return httpApi.post("/api/skills/config", payload, options);
  },
};

export default skillApi;
