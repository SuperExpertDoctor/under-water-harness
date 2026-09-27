export function mergeMissionState(previous, incoming) {
  if (!incoming?.episode_id) return previous;
  const sameEpisode = previous?.episode_id === incoming.episode_id;
  if (sameEpisode && incoming.cursor < previous.cursor) return previous;
  const base = sameEpisode ? previous : { messages: [], jobs: [], plans: [], events: [], agent: {}, cursor: 0 };
  const events = new Map((base.events || []).map((event) => [event.id ?? JSON.stringify(event), event]));
  for (const event of incoming.events || []) events.set(event.id ?? JSON.stringify(event), event);
  const messages = new Map((base.messages || []).map((message) => [message.id, message]));
  for (const message of incoming.messages || []) messages.set(message.id, { ...messages.get(message.id), ...message });
  return { ...base, ...incoming, messages: [...messages.values()].slice(-300), events: [...events.values()].slice(-500) };
}

export function annotationPayload(message, selection) {
  const quote = selection?.trim();
  if (!message?.id || !quote || !message.text?.includes(quote)) return null;
  return { message_id: message.id, quote, plan_id: message.plan_id ?? null };
}

export function interpolateUuv(previous, current, progress) {
  if (!previous || previous.generation !== current.generation) return current;
  return { ...current, position: current.position.map((value, index) => previous.position[index] + (value - previous.position[index]) * progress) };
}

export function filterMissionEvents(events, filters = {}) {
  return events.filter((event) => {
    const data = event.data || {};
    const level = event.level || (/(failed|error|safety)/.test(event.type) ? "error" : /lost|degraded|pending/.test(event.type) ? "warning" : "info");
    return (!filters.uuv || [data.uav_id, data.uuv_id, data.observer_id, ...(data.members || [])].includes(filters.uuv))
      && (!filters.contact || data.contact_id === filters.contact)
      && (!filters.task || [data.plan_id, data.task_id, data.run_id].includes(filters.task))
      && (!filters.level || level === filters.level);
  });
}

export function coalesceMissionEvents(events) {
  const result = [];
  for (const event of events) {
    const previous = result.at(-1);
    const progress = /^(message_update|text_delta|tool_execution_update|agent_heartbeat)$/.test(event.type);
    if (progress && previous?.type === event.type && previous.data?.tool_call_id === event.data?.tool_call_id && previous.data?.message_id === event.data?.message_id) {
      result[result.length - 1] = { ...event, repeat_count: (previous.repeat_count || 1) + 1 };
    } else result.push(event);
  }
  return result;
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
