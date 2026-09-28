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
  if (!message?.id || !quote || quote.length > 2000 || !message.text?.replace(/\s+/g, "").includes(quote.replace(/\s+/g, ""))) return null;
  return { message_id: message.id, quote, plan_id: message.plan_id ?? null };
}

export function groupApprovalsByMessage(messages, plans, events) {
  const requested = new Set((events || []).filter((event) => event.type === "approval_requested").map((event) => event.data?.plan_id));
  const runs = new Map((events || []).filter((event) => event.type === "tool_completed" && event.data?.plan_id && event.data?.run_id)
    .map((event) => [event.data.plan_id, event.data.run_id]));
  const byMessage = new Map();
  const unlinked = [];
  for (const plan of plans || []) {
    if (!plan.plan_id || (plan.status !== "pending_approval" && plan.status !== "rejected" && plan.status !== "expired" && !requested.has(plan.plan_id))) continue;
    const runId = runs.get(plan.plan_id);
    const request = (events || []).find((event) => event.type === "approval_requested" && event.data?.plan_id === plan.plan_id);
    const starts = (events || []).filter((event) => event.type === "message_start" && event.data?.run_id === runId && Number.isFinite(event.id));
    const preceding = Number.isFinite(request?.id) && starts.length
      ? starts.filter((event) => event.id < request.id).at(-1)?.data.message_id : null;
    const message = [...(messages || [])].reverse().find((item) => item.role === "assistant" && item.plan_id === plan.plan_id)
      || (preceding ? (messages || []).find((item) => item.id === preceding) : null)
      || (starts.length && Number.isFinite(request?.id) ? (messages || []).find((item) => item.role === "user" && item.run_id === runId)
        : [...(messages || [])].reverse().find((item) => runId && item.run_id === runId && item.role === "assistant"))
      || (starts.length ? null : [...(messages || [])].reverse().find((item) => runId && item.run_id === runId));
    if (message) byMessage.set(message.id, [...(byMessage.get(message.id) || []), plan]);
    else unlinked.push(plan);
  }
  return { byMessage, unlinked };
}

export function buildDecisionRows(events, plans, episodeId) {
  if (!episodeId || episodeId === "local-demo") return [];
  const relevant = (events || []).filter((event) => event.episode_id === episodeId);
  const byId = new Map((plans || []).filter((plan) => plan.plan_id).map((plan) => [plan.plan_id, plan]));
  for (const event of relevant) if (event.data?.plan_id && !byId.has(event.data.plan_id)) byId.set(event.data.plan_id, { plan_id: event.data.plan_id });
  const actions = { search: "区域搜索", reacquire: "重新搜索", track: "协同跟踪", path: "路径规划" };
  return [...byId.values()].map((plan) => {
    const related = relevant.filter((event) => event.data?.plan_id === plan.plan_id);
    const latest = [...related].reverse().find((event) => ["approval_requested", "approval_decided", "approval_expired", "mission_assignment_committed"].includes(event.type)) || related.at(-1);
    return {
      planId: plan.plan_id,
      members: plan.members || related.find((event) => Array.isArray(event.data?.members))?.data.members || [],
      contactId: plan.contact_id || null,
      action: actions[plan.kind] || "任务计划",
      reason: plan.decision_reason?.trim() || "未提供公开理由",
      timeSeconds: latest ? Number(latest.time) * 60 : Number(plan.created_at_s),
      status: plan.status || "unknown",
      event: latest || null,
      lastEventId: latest?.id || 0,
    };
  }).sort((a, b) => b.lastEventId - a.lastEventId || b.timeSeconds - a.timeSeconds);
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

export function buildDecisionTraces(events, plans, episodeId) {
  if (!episodeId || episodeId === "local-demo") return [];
  const relevant = events.filter((event) => event.episode_id === episodeId);
  const runByPlan = new Map();
  for (const event of relevant) {
    if (event.data?.plan_id && event.data?.run_id && event.type === "tool_completed") {
      runByPlan.set(event.data.plan_id, event.data.run_id);
    }
  }
  const byId = new Map((plans || []).filter((plan) => plan.plan_id).map((plan) => [plan.plan_id, plan]));
  for (const event of relevant) {
    if (event.data?.plan_id && !byId.has(event.data.plan_id)) byId.set(event.data.plan_id, { plan_id: event.data.plan_id });
  }
  return [...byId.values()].map((plan) => {
    const related = relevant.filter((event) => event.data?.plan_id === plan.plan_id);
    const last = (type) => [...related].reverse().find((event) => type.includes(event.type)) || null;
    const runId = runByPlan.get(plan.plan_id) || null;
    const trigger = runId ? relevant.find((event) => event.type === "agent_queued" && event.data?.run_id === runId) || null : null;
    const sourceLabels = { target_found: "目标发现", target_lost: "目标失联", energy_exit: "能源轮换", human: "人工指令", feedback: "用户反馈" };
    const step = (event, label) => ({ event, label: event ? label : "未记录/无法关联" });
    const approval = last(["approval_decided", "approval_expired"]) || last(["approval_requested"]);
    const execution = last(["mission_assignment_committed", "task_completed", "search_complete", "task_failed"]);
    const planEvent = last(["tool_completed"]) || relevant.find((event) => event.type === "tool_result" && event.data?.result_id === plan.result_id) || null;
    const contactId = plan.contact_id || related.find((event) => event.data?.contact_id)?.data.contact_id || null;
    return {
      planId: plan.plan_id, runId, contactId,
      members: plan.members || related.find((event) => Array.isArray(event.data?.members))?.data.members || [],
      status: plan.status || "unknown",
      steps: {
        trigger: step(trigger, `${sourceLabels[trigger?.data?.source] || trigger?.data?.source || "任务"}触发运行`),
        plan: step(planEvent, "候选方案已生成"),
        approval: step(approval, approval?.type === "approval_requested" ? "等待审批" : approval?.type === "approval_expired" ? "审批已过期" : approval?.data?.status === "approved" ? "已批准" : "未批准"),
        execution: step(execution, execution?.type === "mission_assignment_committed" ? "任务已提交" : "执行结果已记录"),
      },
      lastEventId: related.at(-1)?.id ?? -1,
    };
  }).sort((a, b) => b.lastEventId - a.lastEventId);
}

export function coverageDescriptions(frame) {
  const cells = frame?.searchable_cells;
  const area = frame?.coverage_metrics?.fixed_searchable_area_km2;
  const denominator = `${Number.isFinite(cells) ? `${cells} 个可搜索栅格` : "可搜索栅格"}${Number.isFinite(area) ? `，${area} km²` : ""}`;
  const window = frame?.mission_metrics?.coverage_window_min || frame?.coverage_metrics?.primary_window_min || 30;
  return {
    coverage_pct: `历史累计至少扫描一次的栅格占比；分母：${denominator}。`,
    recent_coverage_pct: `最近 ${window} 分钟内被扫描的栅格占比；分母：${denominator}。`,
    effective_coverage_pct: `有效观测覆盖率；分母：${denominator}。`,
    revisit_timeliness_pct: `最近 ${window} 分钟重新扫描的已扫描栅格占比；分母为历史已扫描栅格。`,
  };
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
