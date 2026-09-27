import { useEffect, useRef, useState } from "react";
import { Activity, ArrowDown, BarChart3, Bot, Clipboard, GripHorizontal, Map, Satellite, SlidersHorizontal, X } from "lucide-react";
import { coalesceMissionEvents, filterMissionEvents } from "../state/missionState";

const TABS = [
  { label: "时间线", icon: Activity },
  { label: "Agent 运行", icon: Bot },
  { label: "任务指标", icon: BarChart3 },
  { label: "区域", icon: Map },
  { label: "参数", icon: SlidersHorizontal },
  { label: "AIS", icon: Satellite },
];
const EVENT_NAMES = {
  target_found: "发现目标",
  ship_detected: "舰船确认",
  target_lost: "目标丢失",
  uav_returned: "UAV 返航",
  search_complete: "搜索完成",
  llm_decision: "模型决策",
  route_plan_failed: "航路失败",
  route_replanned: "航路重规划",
  environment_reset: "环境重置",
  mission_assignment_committed: "任务已提交",
  contact_created: "创建接触",
  probe_phase_changed: "调查阶段变化",
  type_i_assessed: "I 类研判",
  type_ii_assessed: "II 类研判",
  assessment_applied: "研判完成",
  probe_timed_out: "调查超时",
  task_failed: "任务失败",
  task_completed: "任务结束",
  tool_started: "工具调用",
  tool_completed: "工具完成",
  tool_failed: "工具失败",
  tool_result: "候选结果",
  approval_requested: "等待审批",
  approval_decided: "审批决定",
  approval_expired: "审批过期",
  agent_started: "模型开始",
  agent_completed: "模型完成",
  agent_failed: "模型异常",
  safety_pause: "安全暂停",
  message_start: "消息开始",
  message_update: "消息流式更新",
  message_end: "消息完成",
  tool_execution_start: "工具开始",
  tool_execution_update: "工具进度",
  tool_execution_end: "工具结束",
  agent_start: "Agent 回合开始",
  agent_end: "Agent 回合结束",
  agent_settled: "Agent 已收尾",
  compaction_start: "上下文压缩开始",
  compaction_end: "上下文压缩完成",
  auto_retry_start: "自动重试",
  auto_retry_end: "重试结束",
  turn_start: "模型轮次开始",
  turn_end: "模型轮次结束",
  queue_update: "队列更新",
  agent_queued: "Agent 已排队",
  tracking_established: "协同跟踪建立",
  contact_state_changed: "接触状态变化",
  tracking_position_reached: "跟踪入位",
  tracking_relief_required: "请求跟踪接替",
  coverage_repartitioned: "搜索区域调整",
  energy_exit_started: "低能量退出",
  uuv_replenished: "新代次补入",
  energy_authorization_required: "能源轮换待授权",
  allocation_blocked: "分区不可行",
};

export default function BottomDrawer({ frame, events = [], llmCycle, visible, onToggle, mission, onSelectEvent }) {
  const [activeTab, setActiveTab] = useState(0);
  const [height, setHeight] = useState(220);
  const [config, setConfig] = useState(null);
  const [configError, setConfigError] = useState("");
  const drag = useRef(null);

  useEffect(() => {
  if (activeTab !== 4 || config || configError) return;
    fetch("/api/config")
      .then((response) => {
        if (!response.ok) throw new Error();
        return response.json();
      })
      .then(setConfig)
      .catch(() => setConfigError("参数接口不可用"));
  }, [activeTab, config, configError]);

  useEffect(() => {
    const move = (event) => {
      if (!drag.current) return;
      setHeight(Math.max(180, Math.min(window.innerHeight * 0.58, drag.current.height + drag.current.y - event.clientY)));
    };
    const up = () => { drag.current = null; };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, []);

  if (!visible) return null;
  return (
    <section className="bottom-drawer" style={{ height }} aria-label="任务详情">
      <button className="drawer-grip" onPointerDown={(event) => { drag.current = { y: event.clientY, height }; }} onKeyDown={(event) => { if (["ArrowUp", "ArrowDown"].includes(event.key)) { event.preventDefault(); setHeight((current) => Math.max(180, Math.min(window.innerHeight * .58, current + (event.key === "ArrowUp" ? 24 : -24)))); } }} aria-label="调整面板高度" title="拖动调整高度"><GripHorizontal size={20} /></button>
      <div className="drawer-tabs" role="tablist">
        {TABS.map(({ label, icon: Icon }, index) => (
          <button key={label} role="tab" aria-selected={index === activeTab} className={index === activeTab ? "active" : ""} onClick={() => setActiveTab(index)}>
            <Icon size={15} />{label}
          </button>
        ))}
        <button className="drawer-close" onClick={onToggle} aria-label="关闭任务详情" title="关闭"><X size={16} /></button>
      </div>
      <div className="drawer-content">
        {activeTab === 0 && <TimelineTab events={events} frame={frame} onSelectEvent={onSelectEvent} />}
        {activeTab === 1 && <AgentTab events={events} mission={mission} llm={llmCycle} onSelectEvent={onSelectEvent} />}
        {activeTab === 2 && <MetricsTab frame={frame} />}
        {activeTab === 3 && <RegionTab frame={frame} />}
        {activeTab === 4 && <ParamsTab config={config} error={configError} />}
        {activeTab === 5 && <AisTab frame={frame} />}
      </div>
    </section>
  );
}

function TimelineTab({ events, frame, onSelectEvent }) {
  const [filters, setFilters] = useState({ uuv: "", contact: "", task: "", level: "" });
  const [following, setFollowing] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const scroll = useRef(null);
  const lastId = useRef(events.at(-1)?.id);
  const visible = coalesceMissionEvents(filterMissionEvents(events, filters)).slice(-180);
  useEffect(() => {
    const changed = lastId.current !== events.at(-1)?.id;
    lastId.current = events.at(-1)?.id;
    if (following && scroll.current) { scroll.current.scrollTop = scroll.current.scrollHeight; setUnseen(0); }
    else if (changed) setUnseen((count) => count + 1);
  }, [events, following]);
  if (!events.length) return <EmptyState text="暂无任务事件" />;
  return (
    <div className="timeline-view">
      <div className="event-filters">
        <select aria-label="按艇筛选" value={filters.uuv} onChange={(event) => setFilters({ ...filters, uuv: event.target.value })}><option value="">全部艇</option>{(frame?.uavs || []).map((uuv) => <option key={uuv.id}>{uuv.id}</option>)}</select>
        <select aria-label="按接触筛选" value={filters.contact} onChange={(event) => setFilters({ ...filters, contact: event.target.value })}><option value="">全部接触</option>{[...new Set(events.map((event) => event.data?.contact_id).filter(Boolean))].map((id) => <option key={id}>{id}</option>)}</select>
        <select aria-label="按任务筛选" value={filters.task} onChange={(event) => setFilters({ ...filters, task: event.target.value })}><option value="">全部任务</option>{[...new Set(events.flatMap((event) => [event.data?.plan_id, event.data?.task_id, event.data?.run_id]).filter(Boolean))].map((id) => <option key={id}>{id}</option>)}</select>
        <select aria-label="按级别筛选" value={filters.level} onChange={(event) => setFilters({ ...filters, level: event.target.value })}><option value="">全部级别</option><option value="info">信息</option><option value="warning">警告</option><option value="error">错误</option></select>
        {!following && <button className="follow-latest" onClick={() => setFollowing(true)}><ArrowDown size={13} />{unseen ? `${unseen} 次更新` : "跟随最新"}</button>}
      </div>
      <div className="timeline-list" ref={scroll} onScroll={(event) => { const node = event.currentTarget; setFollowing(node.scrollHeight - node.scrollTop - node.clientHeight < 30); }}>
      {!visible.length && <EmptyState text="没有符合筛选的事件" />}
      {visible.map((event, index) => (
        <details className="timeline-detail" key={event.id || `${event.time}-${event.type}-${index}`}>
        <summary className={`timeline-item event-${event.type}`} onClick={() => onSelectEvent?.(event)}>
          <time>{Number(event.sim_time_s ?? event.time_s ?? (event.time || 0) * 60).toFixed(1)} s</time>
          <i />
          <strong>{EVENT_NAMES[event.type] || event.type}{event.repeat_count > 1 ? ` ×${event.repeat_count}` : ""}</strong>
          <span>{event.data?.tool_name || event.data?.tool || event.data?.plan_id || event.data?.uuv_id || event.data?.uav_id || event.data?.contact_id || event.data?.ship_id || event.data?.group_id || ""}</span>
        </summary><pre>{JSON.stringify(event.data || {}, null, 2)}</pre></details>
      ))}</div>
    </div>
  );
}

function AgentTab({ events, mission, llm, onSelectEvent }) {
  const activity = coalesceMissionEvents(events.filter((event) => /agent|tool|message|turn|compaction|queue/.test(event.type))).slice(-100).reverse();
  return <div className="agent-runtime"><div className="runtime-facts"><span>状态 <b>{mission?.state.agent?.status || "--"}</b></span><span>模型 <b>{mission?.state.agent?.model || "--"}</b></span><span>排队 <b>{mission?.state.jobs?.filter((job) => job.status === "queued").length || 0}</b></span></div>
    {!activity.length && <EmptyState text="暂无 Agent 运行事件" />}
    {activity.map((event) => <details className="tool-output" key={event.id}><summary onClick={() => onSelectEvent?.(event)}><time>{Number(event.sim_time_s ?? event.time_s ?? (event.time || 0) * 60).toFixed(1)} s</time> {EVENT_NAMES[event.type] || event.type} · {event.data?.tool_name || event.data?.tool || event.data?.reason || ""}{Number.isFinite(event.data?.duration_ms) && <span> · 墙钟 {event.data.duration_ms} ms</span>}</summary><pre>{JSON.stringify(event.data || {}, null, 2)}</pre></details>)}
    {llm && <details className="workspace-details"><summary>历史模型记录</summary><LLMTab llm={llm} /></details>}
  </div>;
}

export function MetricsTab({ frame }) {
  const metrics = frame?.mission_metrics || {};
  const names = { search_boats: "搜索艇", search_regions: "搜索区", effective_tracking_seconds: "有效协同跟踪 / s", lost_seconds: "丢失时长 / s", handoff_count: "已完成接替", handoff_attempts: "已发起接替", rotation_count: "轮换次数", coverage_pct: "覆盖率 / %", effective_coverage_pct: "有效覆盖率 / %", recent_coverage_pct: "近30分钟覆盖率 / %", revisit_timeliness_pct: "30分钟重访及时率 / %", unscanned_cells: "未扫描积压 / 格", handoff_success_rate: "接替成功率 / %" };
  return <div className="mission-metrics"><dl>{Object.entries(metrics).map(([key, value]) => <div key={key}><dt>{names[key] || key}</dt><dd>{value == null ? "无数据" : typeof value === "number" ? Number(value.toFixed(2)) : String(value)}</dd></div>)}</dl>{!Object.keys(metrics).length && <EmptyState text="暂无任务指标" />}
    {frame?.standing_policy && <details className="workspace-details"><summary>常驻授权策略 · {frame.standing_policy.enabled ? "已授权" : "未启用"}</summary><pre>{JSON.stringify(frame.standing_policy, null, 2)}</pre></details>}
  </div>;
}

function RegionTab({ frame }) {
  const rows = [
    ...(frame?.search_regions || []).map((region) => ({ ...region, displayType: "搜索" })),
    ...(frame?.track_regions || []).map((region) => ({ ...region, displayType: "跟踪" })),
  ];
  if (!rows.length) return <EmptyState text="尚未划分任务区域" />;
  return (
    <div className="table-wrap">
      <table className="region-table">
        <thead><tr><th>ID</th><th>类型</th><th>边界</th><th>优先级</th><th>信息素</th><th>价值</th><th>完成</th><th>执行单元</th></tr></thead>
        <tbody>{rows.map((region) => (
          <tr key={region.id}>
            <td><b>{region.id}</b></td><td>{region.displayType}</td><td className="mono">[{region.bbox?.join(", ")}]</td>
            <td><span className={`priority ${region.priority || "high"}`}>{region.priority || "持续"}</span></td>
            <td>{Number(region.avg_info || 0).toFixed(2)}</td><td>{Number(region.info_value || 0).toFixed(2)}</td>
            <td>{region.displayType === "搜索" ? `${Math.round(region.completion_pct || 0)}%` : "-"}</td><td>{region.assigned_uav_id || "待分配"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function LLMTab({ llm }) {
  const copy = (text) => navigator.clipboard?.writeText(text || "");
  if (!llm) return <EmptyState text="暂无模型运行记录" />;
  const sections = [
    ["System Prompt", llm.system_prompt],
    ["User Prompt", llm.user_prompt],
    ["Response", llm.response || llm.attempts?.at(-1)?.response],
    ["Validation", JSON.stringify(llm.validation, null, 2)],
  ];
  return (
    <div className="llm-log">
      <div className="llm-log-head"><div><span className={llm.success ? "success" : "failed"}>{llm.success ? "VALID" : "FAILED"}</span><strong>{llm.model}</strong></div><small>{llm.attempts?.length || 0} attempts</small></div>
      <div className="llm-sections">{sections.map(([label, content], index) => (
        <details key={label} open={index === 2}>
          <summary>{label}<button onClick={(event) => { event.preventDefault(); copy(content); }} aria-label={`复制 ${label}`} title="复制"><Clipboard size={14} /></button></summary>
          <pre>{content || "无内容"}</pre>
        </details>
      ))}</div>
    </div>
  );
}

function AisTab({ frame }) {
  const rows = frame?.contacts?.length
    ? frame.contacts.map((contact) => {
      const ais = [...(contact.samples || [])].reverse().find((sample) => sample.source === "ais");
      return { id: contact.contact_id, mmsi: contact.ais_mmsi, aisPosition: ais?.position, position: contact.estimated_position, state: contact.vessel_class || "unknown" };
    })
    : (frame?.ships || []).map((ship) => ({
      id: ship.id,
      mmsi: ship.ais?.mmsi,
      aisPosition: ship.ais?.reported_position,
      position: ship.estimated_position,
      state: "historical",
    }));
  return (
    <div className="table-wrap">
      <table className="region-table ais-table">
        <thead><tr><th>接触</th><th>MMSI</th><th>AIS 位置</th><th>估计位置</th><th>样本</th><th>状态</th></tr></thead>
        <tbody>{rows.map((contact) => (
          <tr key={contact.id}>
            <td><b>{contact.id}</b></td>
            <td>{contact.mmsi || "无"}</td>
            <td className="mono">{formatPosition(contact.aisPosition)}</td>
            <td className="mono">{formatPosition(contact.position)}</td>
            <td>{frame?.contacts?.length ? (frame.contacts.find((item) => item.contact_id === contact.id)?.samples?.length || 0) : "-"}</td>
            <td>{contact.state === "unknown" ? "待核查" : contact.state === "type_i" ? "I 类船舶" : contact.state === "type_ii" ? "II 类船舶" : "历史帧"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function formatPosition(position) {
  return Array.isArray(position) ? position.map((value) => Number(value).toFixed(1)).join(", ") : "-";
}

function ParamsTab({ config, error }) {
  if (error) return <EmptyState text={error} />;
  if (!config) return <div className="loading-state"><span />加载参数</div>;
  return <div className="params-grid">{Object.entries(config).map(([section, values]) => (
    <section key={section}><h3>{section}</h3>{Object.entries(values && typeof values === "object" && !Array.isArray(values) ? values : { value: values }).map(([key, value]) => (
      <div key={key}><span>{key}</span><b>{formatParamValue(value)}</b></div>
    ))}</section>
  ))}</div>;
}

function formatParamValue(value) {
  if (Array.isArray(value)) return value.join(" × ");
  if (value && typeof value === "object") {
    return Object.entries(value)
      .map(([key, child]) => `${key}: ${formatParamValue(child)}`)
      .join("; ");
  }
  return String(value);
}

function EmptyState({ text }) {
  return <div className="tab-empty">{text}</div>;
}
