import httpApi from "./httpApi";
import { mutationPayload } from "../../state/missionState";

export const missionApi = {
  health: () => httpApi.getHealth(),
  get: (path) => httpApi.get(path),
  mutate(path, episode, readOnly, data, method = "post") {
    return httpApi[method](path, mutationPayload(episode, readOnly, data), { timeoutMs: 120000 });
  },
};
