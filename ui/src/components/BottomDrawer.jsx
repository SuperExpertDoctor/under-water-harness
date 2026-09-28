import { useEffect, useRef, useState } from "react";
import { Activity, BarChart3, Bot, Clipboard, GripHorizontal, Map, Radar, X } from "lucide-react";
import { buildAgentRuntimeRows, buildDecisionRows, buildTimelineRows, coverageDescriptions } from "../state/missionState";

const TABS = [
  { label: "决策过程", icon: Activity },
  { label: "时间线", icon: Activity },
  { label: "Agent 运行", icon: Bot },
  { label: "任务指标", icon: BarChart3 },
  { label: "区域", icon: Map },
  { label: "目标状态", icon: Radar },
];
export default function BottomDrawer({ frame, events = [], llmCycle, visible, onToggle, mission, onSelectEvent, onSelectDecision, readOnly }) {
  const [activeTab, setActiveTab] = useState(0);
  const [height, setHeight] = useState(220);
  const drag = useRef(null);

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
          <button key={label} role="tab" aria-label={label} title={label} aria-selected={index === activeTab} className={index === activeTab ? "active" : ""} onClick={() => setActiveTab(index)}>
            <Icon size={15} />{label}
          </button>
        ))}
        <select className="drawer-more mobile-only" aria-label="更多任务视图" value={activeTab >= 4 ? activeTab : ""} onChange={(event) => setActiveTab(Number(event.target.value))}>
          <option value="" disabled>更多视图</option>
          {TABS.slice(4).map(({ label }, index) => <option value={index + 4} key={label}>{label}</option>)}
        </select>
        <button className="drawer-close" onClick={onToggle} aria-label="关闭任务详情" title="关闭"><X size={16} /></button>
      </div>
      <div className="drawer-content">
        {activeTab === 0 && <DecisionTab frame={frame} events={events} plans={readOnly ? frame?.plans : mission?.state.plans} onSelectEvent={(event) => { onSelectEvent?.(event); setActiveTab(1); }} onSelectDecision={onSelectDecision} />}
        {activeTab === 1 && <TimelineTab events={events} plans={readOnly ? frame?.plans : mission?.state.plans} frame={frame} onSelectDecision={onSelectDecision} />}
        {activeTab === 2 && <AgentTab events={events} frame={frame} mission={mission} llm={llmCycle} />}
        {activeTab === 3 && <MetricsTab frame={frame} onShowRegions={() => setActiveTab(4)} />}
        {activeTab === 4 && <RegionTab frame={frame} onSelectEvent={onSelectEvent} />}
        {activeTab === 5 && <TargetStatusTab frame={frame} />}
      </div>
    </section>
  );
}

function DecisionTab({ frame, events, plans, onSelectEvent, onSelectDecision }) {
  const rows = buildDecisionRows(events, plans, frame?.episode_id);
  if (!rows.length) return <EmptyState text={frame?.episode_id === "local-demo" ? "本地演示帧没有真实决策记录" : "尚无调度记录"} />;
  const states = { pending_approval: "待审批", active: "执行中", rejected: "已拒绝", expired: "已过期", superseded: "已接替", approved: "已批准" };
  return <div className="decision-table-wrap" aria-label="实时调度决策">
    <table className="decision-table"><thead><tr><th>时间</th><th>决策/调度</th><th>为什么</th><th>参与 UUV</th><th>状态</th><th>记录</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.planId}>
        <td data-label="时间" className="decision-time">{Number.isFinite(row.timeSeconds) ? `${row.timeSeconds.toFixed(1)} s` : "未记录"}</td>
        <td data-label="决策/调度"><button type="button" className="decision-focus" onClick={() => onSelectDecision?.(row)} title={`定位计划 ${row.planId}`}>{row.action}{row.contactId ? ` · ${row.contactId}` : ""}<small>{row.planId}</small></button></td>
        <td data-label="为什么" className="decision-reason">{row.reason}</td><td data-label="参与 UUV">{row.members.join("、") || "未记录"}</td>
        <td data-label="状态"><span className={`decision-status ${row.status}`}>{states[row.status] || row.status}</span></td>
        <td data-label="记录">{row.event ? <button className="decision-source" type="button" onClick={() => onSelectEvent?.(row.event)}>#{row.event.id}</button> : "-"}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function TimelineTab({ events, frame, plans, onSelectDecision }) {
  const { rows, unlinked } = buildTimelineRows(events, plans, frame?.episode_id);
  return <div className="decision-table-wrap">
    {unlinked > 0 && <p className="table-warning">{unlinked} 条历史决策缺少运行关联，无法确定触发类型</p>}
    {events[0]?.id > 1 && <p className="table-warning">部分早期事件已截断，历史可能不完整</p>}
    {!rows.length ? <EmptyState text="尚无完成的调度决策" /> : <table className="decision-table mission-log-table"><thead><tr><th>决策时间</th><th>触发类型</th><th>决策原因</th><th>决策内容</th><th>参与调度的 UUV</th></tr></thead>
      <tbody>{rows.map((row) => <tr key={row.id}>
        <td data-label="决策时间" className="decision-time">{row.timeSeconds.toFixed(1)} s</td><td data-label="触发类型">{row.trigger}</td>
        <td data-label="决策原因" className="decision-reason">{row.reason}</td>
        <td data-label="决策内容">{row.planId ? <button type="button" className="decision-focus" onClick={() => onSelectDecision?.(row)}>{row.action}<small>{row.planId}</small></button> : row.action}</td>
        <td data-label="参与调度的 UUV">{row.members.join("、") || "无新增调度"}</td>
      </tr>)}</tbody></table>}
  </div>;
}

function AgentTab({ events, frame, mission, llm }) {
  const rows = buildAgentRuntimeRows(events, frame?.episode_id);
  return <div className="agent-runtime decision-table-wrap">
    <div className="runtime-facts"><span>Worker <b>{({ offline: "离线", idle: "空闲", running: "运行中", degraded: "异常" })[mission?.state.agent?.status || frame?.agent_status?.status] || "离线"}</b></span><span>模型 <b>{mission?.state.agent?.model || frame?.agent_status?.model || "--"}</b></span></div>
    {events[0]?.id > 1 && <p className="table-warning">部分早期事件已截断，历史可能不完整</p>}
    {!rows.length ? <EmptyState text="暂无 Agent 运行记录" /> : <table className="decision-table mission-log-table"><thead><tr><th>时间</th><th>运行</th><th>状态</th><th>当前步骤/结果</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}>
      <td data-label="时间" className="decision-time">{row.timeSeconds.toFixed(1)} s</td><td data-label="运行" className="mono">{row.runId}</td><td data-label="状态">{row.status}</td><td data-label="当前步骤/结果">{row.detail}</td>
    </tr>)}</tbody></table>}
    {llm && <details className="workspace-details"><summary>历史模型记录</summary><LLMTab llm={llm} /></details>}
  </div>;
}

export function MetricsTab({ frame, onShowRegions }) {
  const metrics = frame?.mission_metrics || {};
  const window = metrics.coverage_window_min || frame?.coverage_metrics?.primary_window_min || 30;
  const descriptions = coverageDescriptions(frame);
  const value = (number, suffix) => Number.isFinite(number) ? `${Number(number.toFixed(1))}${suffix}` : "尚无数据";
  const handoffs = metrics.handoff_attempts == null ? "历史记录不完整" : metrics.handoff_attempts === 0 ? "尚无接替" : `${metrics.handoff_count} / ${metrics.handoff_attempts}`;
  return <div className="mission-metrics"><dl>
    <div><dt>持续区域覆盖率 · 近 {window} 分钟</dt><dd>{value(metrics.recent_coverage_pct, "%")}</dd><details className="metric-definition"><summary>计算口径</summary><p>{descriptions.recent_coverage_pct}</p><button type="button" onClick={onShowRegions}>查看区域</button></details></div>
    <div><dt>单次目标有效跟踪时间</dt><dd>{value(metrics.single_tracking_seconds, " s")}</dd></div>
    <div><dt>当前目标失联时长</dt><dd>{Number.isFinite(metrics.current_lost_seconds) ? value(metrics.current_lost_seconds, " s") : frame?.contacts?.length ? "未失联" : "尚未发现目标"}</dd></div>
    <div><dt>跟踪接替成功情况</dt><dd>{handoffs}</dd></div>
  </dl></div>;
}

function regionInformationMean(matrix, cells) {
  if (!cells?.length) return "--";
  const values = cells.map(([col, row]) => matrix?.[col]?.[row]);
  if (!values.every(Number.isFinite)) return "--";
  return `${(100 * values.reduce((sum, value) => sum + Math.max(0, Math.min(1, value)), 0) / values.length).toFixed(1)}%`;
}

export function RegionTab({ frame, onSelectEvent }) {
  const rows = [
    ...(frame?.search_regions || []).map((region) => ({ ...region, displayType: "搜索" })),
    ...(frame?.track_regions || []).map((region) => ({ ...region, displayType: "跟踪" })),
  ];
  if (!rows.length) return <EmptyState text="尚未划分任务区域" />;
  return (
    <div className="table-wrap">
      <table className="region-table">
        <thead><tr><th>ID</th><th>类型</th><th>边界</th><th>优先级</th><th>平均新鲜度</th><th>平均线索</th><th>完成</th><th>执行单元</th></tr></thead>
        <tbody>{rows.map((region) => (
          <tr key={region.id}>
            <td><button className="region-focus" type="button" onClick={() => onSelectEvent?.({ data: { uav_id: region.assigned_uav_id } })} title={`定位 ${region.id} 执行单元`}>{region.id}</button></td><td>{region.displayType}</td><td className="mono">[{region.bbox?.join(", ")}]</td>
            <td><span className={`priority ${region.priority || "high"}`}>{region.priority || "持续"}</span></td>
            <td>{regionInformationMean(frame.info_matrix, region.cells)}</td><td>{regionInformationMean(frame.target_info_matrix, region.cells)}</td>
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

function TargetStatusTab({ frame }) {
  const rows = frame?.target_maneuver_history || [];
  return <div className="decision-table-wrap">
    {rows.length >= 200 && <p className="table-warning">早期目标机动记录已截断</p>}
    {!rows.length ? <EmptyState text="暂无目标机动参数变化" /> : <table className="decision-table mission-log-table"><thead><tr><th>时间</th><th>机动参数变化的原因</th><th>机动模型参数</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.run_id || "system"}-${row.time_s}-${index}`}>
      <td data-label="时间" className="decision-time">{Number(row.time_s).toFixed(1)} s</td>
      <td data-label="机动参数变化的原因">{row.source === "system" ? `系统回退 · ${row.reason}` : row.reason}</td>
      <td data-label="机动模型参数" className="mono">速度 {Number(row.speed_mps).toFixed(1)} m/s · 转向偏置 {Number(row.turn_bias).toFixed(2)} · {row.duration_s == null ? "持续：默认控制" : `持续 ${Number(row.duration_s).toFixed(0)} s`}</td>
    </tr>)}</tbody></table>}
  </div>;
}

function EmptyState({ text }) {
  return <div className="tab-empty">{text}</div>;
}
