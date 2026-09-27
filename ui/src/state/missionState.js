export function mergeMissionState(previous, incoming) {
  if (!incoming?.episode_id) return previous;
  const sameEpisode = previous?.episode_id === incoming.episode_id;
  if (sameEpisode && incoming.cursor < previous.cursor) return previous;
  const base = sameEpisode ? previous : { messages: [], jobs: [], plans: [], events: [], agent: {}, cursor: 0 };
  const events = new Map((base.events || []).map((event) => [event.id ?? JSON.stringify(event), event]));
  for (const event of incoming.events || []) events.set(event.id ?? JSON.stringify(event), event);
  return { ...base, ...incoming, events: [...events.values()].slice(-500) };
}

export function mergeTelemetryFrame(previous, incoming) {
  if (previous?.episode_id !== incoming.episode_id) return incoming;
  if (incoming.frame_id < previous.frame_id) return previous;
  return { ...previous, ...incoming };
}

export function mutationPayload(episode, readOnly, data = {}) {
  if (readOnly) throw new Error("readonly");
  if (!episode || episode === "local-demo") throw new Error("live_episode_required");
  return { ...data, episode_id: episode };
}

export function selectionToMeters(bbox, area) {
  const cell = area.cell_size_km * 1000;
  const height = area.height_km * 1000;
  return [bbox[0] * cell, height - bbox[3] * cell, bbox[2] * cell, height - bbox[1] * cell];
}

export function routeToCells(points, area) {
  const cell = area.cell_size_km * 1000;
  const height = area.height_km * 1000;
  return points.map(([x, y]) => [x / cell - .5, (height - y) / cell - .5]);
}
