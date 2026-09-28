import { useState } from "react";
import { Check, Eye, Play, Send, Square, X, Workflow } from "lucide-react";
import { selectionToMeters } from "../state/missionState";

export function planningRequest(tool, automatic, members, bbox, contactId) {
  const request = { tool, ...(!automatic ? { members } : {}) };
  if (tool === "plan_search") {
    request.standing_policy = automatic;
    if (!automatic) request.bbox = bbox;
  }
  if (tool === "plan_tracking") request.contact_id = contactId;
  return request;
}

export default function AgentPanel({ tab, mission, frame, readOnly, selection }) {
  const [text, setText] = useState("");
  const [members, setMembers] = useState([]);
  const [bounds, setBounds] = useState([250, 250, 3750, 3750]);
  const [tool, setTool] = useState("plan_search");
  const [contactId, setContactId] = useState("");
  const [taskLabel, setTaskLabel] = useState("区域搜索");
  const [automaticMembers, setAutomaticMembers] = useState(true);
  const disabled = readOnly || !mission.ready || !frame?.episode_id || frame?.episode_id === "local-demo" || Boolean(mission.busy);
  const candidate = mission.candidate;
  const assessment = mission.assessment;
  const plans = mission.state.plans || [];
  const pending = plans.filter((plan) => plan.status === "pending_approval");
  const jobs = mission.state.jobs || [];
  const selectedMembers = members.length ? members : (frame?.uavs || []).slice(0, 3).map((uuv) => uuv.id);
  const activeJobs = jobs.filter((job) => ["running", "queued"].includes(job.status));

  if (readOnly) return <div className="panel-empty">回放只读</div>;

  if (tab === "chat") return <div className="agent-panel">
    <section className="agent-conversation" aria-label="PI 对话" aria-live="polite">
      {(mission.state.messages || []).length === 0 && <p className="panel-empty">暂无对话</p>}
      {(mission.state.messages || []).map((message) => <article className={`agent-message ${message.role}`} key={message.id}>
        <header><strong>{message.role === "user" ? "操作员" : "PI Agent"}</strong><small>{message.model || ""}</small></header>
        <p>{message.text}</p>
      </article>)}
    </section>
    {mission.state.agent?.error && <p className="panel-error" role="alert">{mission.state.agent.error}</p>}
    {activeJobs.map((job) => <div className="mission-list-row" key={job.run_id}><span>{job.status === "queued" ? "等待模型" : "模型决策中"}<small>{job.text}</small></span>
      <button className="icon-btn" title="取消决策" aria-label="取消决策" disabled={disabled} onClick={() => mission.act("取消决策", `/api/pi-agent/task-assignment/${encodeURIComponent(job.run_id)}/cancel`)}><Square size={15} /></button></div>)}
    <form className="chat-composer" onSubmit={async (event) => {
      event.preventDefault();
      if (!text.trim()) return;
      const result = await mission.act("发送消息", "/api/pi-agent/messages", { text: text.trim() });
      if (result) setText("");
    }}>
      <label htmlFor="mission-message">任务指令</label>
      <textarea id="mission-message" value={text} onChange={(event) => setText(event.target.value)} maxLength={4000} rows={4} disabled={disabled} />
      <button className="primary-action" disabled={disabled || !text.trim()}><Send size={15} />发送</button>
    </form>
  </div>;

  if (tab === "approvals") return <div className="agent-panel">
    <div className="section-heading"><span>待审批计划</span><small>{pending.length}</small></div>
    {!pending.length && <p className="panel-empty">暂无待审批计划</p>}
    {pending.map((plan) => <article className="approval-item" key={plan.plan_id}>
      <strong>{plan.kind} · {plan.members?.join(", ")}</strong>
      <dl className="plan-facts"><div><dt>计划</dt><dd>{plan.plan_id}</dd></div><div><dt>风险</dt><dd>{plan.risk}</dd></div>
        <div><dt>原因</dt><dd>{plan.reason}</dd></div><div><dt>期限</dt><dd>{Math.max(0, plan.expires_at_s - (frame?.sim_time_min || 0) * 60).toFixed(0)} s</dd></div>
        <div className="execution-domain"><dt>授权范围（含转场）</dt><dd>{plan.execution_domain?.map((value) => Number(value).toFixed(0)).join(", ") || "未提供"} m</dd></div>
        <div><dt>范围策略</dt><dd>{plan.domain_policy || "未提供"}</dd></div>
        <div><dt>回退</dt><dd>{plan.fallback}</dd></div><div><dt>任务版本</dt><dd>{plan.mission_revision}</dd></div></dl>
      <div className="mission-actions">
        <button className="primary-action" onClick={() => mission.preview(plan.plan_id)}><Eye size={14} />预览</button>
        <button className="primary-action" disabled={disabled} onClick={() => mission.act("批准计划", `/api/approvals/${encodeURIComponent(plan.plan_id)}/decision`, { decision: "approve" })}><Check size={14} />批准</button>
        <button className="primary-action danger-action" disabled={disabled} onClick={() => mission.act("拒绝计划", `/api/approvals/${encodeURIComponent(plan.plan_id)}/decision`, { decision: "reject" })}><X size={14} />拒绝</button>
      </div>
    </article>)}
    <div className="section-heading"><span>计划状态</span></div>
    {[...plans].reverse().slice(0, 20).map((plan) => <div className="mission-list-row" key={plan.plan_id}><span>{plan.kind} · {plan.members?.join(", ")}<small>{plan.status} · {plan.plan_id}</small></span><button className="icon-btn" onClick={() => mission.preview(plan.plan_id)} aria-label={`预览 ${plan.plan_id}`} title="预览计划"><Eye size={15} /></button></div>)}
  </div>;

  return <div className="agent-panel">
    <section className="mission-section" aria-label="算法规划">
      <div className="section-heading"><span><Workflow size={15} />算法规划</span></div>
      <label className="mission-field">计算类型<select value={tool} onChange={(event) => setTool(event.target.value)} disabled={disabled}>
        <option value="plan_search">搜索航线</option><option value="plan_tracking">目标跟踪</option><option value="compute_task_allocation">编队分配</option>
      </select></label>
      {tool !== "compute_task_allocation" && <label className="automatic-members"><input type="checkbox" checked={automaticMembers} onChange={(event) => setAutomaticMembers(event.target.checked)} disabled={disabled} />{tool === "plan_search" ? "全艇队原子搜索计划" : "自动选择协同跟踪队"}</label>}
      {tool !== "compute_task_allocation" && !automaticMembers && <fieldset className="member-picker"><legend>{tool === "plan_tracking" ? "编队成员（最多 3 艘）" : "搜索艇"}</legend>
        {(frame?.uavs || []).map((uuv) => <label key={uuv.id}><input type="checkbox" checked={selectedMembers.includes(uuv.id)} disabled={disabled || (!selectedMembers.includes(uuv.id) && selectedMembers.length >= 3)} onChange={(event) => {
          const next = event.target.checked ? [...selectedMembers, uuv.id] : selectedMembers.filter((id) => id !== uuv.id);
          setMembers(next.length ? next : [uuv.id]);
        }} />{uuv.id}</label>)}
      </fieldset>}
      {tool === "plan_search" && !automaticMembers && <>
        <fieldset className="bounds-fields"><legend>搜索边界（米）</legend>{["X 最小", "Y 最小", "X 最大", "Y 最大"].map((label, index) => <label key={label}>{label}<input type="number" step="50" min="0" max="4000" value={bounds[index]} disabled={disabled} onChange={(event) => setBounds((current) => current.map((value, i) => i === index ? Number(event.target.value) : value))} /></label>)}</fieldset>
        {selection && <button className="primary-action" disabled={disabled} onClick={() => setBounds(selectionToMeters(selection, frame.task_area))}>采用地图框选</button>}
      </>}
      {tool === "plan_tracking" && <label className="mission-field">观测接触<select value={contactId} disabled={disabled} onChange={(event) => setContactId(event.target.value)}><option value="">选择已确认接触</option>{(frame?.contacts || []).filter((contact) => !["tentative", "lost"].includes(contact.state)).map((contact) => <option key={contact.contact_id} value={contact.contact_id}>{contact.contact_id} · {contact.state}</option>)}</select></label>}
      <button className="primary-action" disabled={disabled || (tool === "plan_tracking" && !contactId) || (tool === "plan_search" && !automaticMembers && (bounds[0] >= bounds[2] || bounds[1] >= bounds[3]))} onClick={async () => {
        mission.setCandidate(null); mission.setAssessment(null);
        const result = await mission.act("计算候选", "/api/algorithm/task-plan", planningRequest(tool, automaticMembers, selectedMembers, bounds, contactId));
        if (result?.result_id) await mission.preview(result.result_id);
      }}><Play size={14} />计算候选</button>
      {candidate && <div className="candidate-detail">
        <div className="section-heading"><span>候选结果</span><button className="icon-btn" title="清除预览" aria-label="清除预览" onClick={() => { mission.setCandidate(null); mission.setAssessment(null); }}><X size={14} /></button></div>
        <p>{candidate.algorithm} · {candidate.status}</p><small>{candidate.result_id}</small>
        {candidate.execution_domain && <p>授权范围（含转场）：[{candidate.execution_domain.map((value) => Number(value).toFixed(0)).join(", ")}] m</p>}
        {candidate.domain_policy && <small>{candidate.domain_policy}</small>}
        {(candidate.teams || []).map((team) => <p key={team.id}>{team.task_id}: {team.members.join(", ")}</p>)}
        {candidate.diagnostics && Object.keys(candidate.diagnostics).length > 0 && <pre>{JSON.stringify(candidate.diagnostics, null, 2)}</pre>}
        <div className="mission-actions"><button className="primary-action" disabled={disabled || candidate.status !== "succeeded"} onClick={async () => {
          const result = await mission.act("校验候选", "/api/algorithm/decision", { tool: "evaluate_plan", result_id: candidate.result_id });
          if (result) mission.setAssessment(result);
        }}><Check size={14} />校验</button>
        <button className="primary-action" disabled={disabled || !assessment?.valid || Boolean(candidate.plan_id)} onClick={async () => {
          const result = await mission.act("提交计划", "/api/algorithm/commands", { result_id: candidate.result_id, command_id: crypto.randomUUID() });
          if (result) { mission.setCandidate({ ...candidate, ...result }); mission.setAssessment(null); }
        }}><Send size={14} />提交权限检查</button></div>
        {assessment && <p className={assessment.valid ? "success" : "panel-error"} role="status">{assessment.valid ? `校验通过 · 风险 ${assessment.risk} · ${assessment.requires_approval ? "需要审批" : "允许执行"}` : assessment.errors?.join(", ")}</p>}
        <label className="mission-field">任务名称<input value={taskLabel} maxLength={100} onChange={(event) => setTaskLabel(event.target.value)} disabled={disabled} /></label>
        <button className="primary-action" disabled={disabled || !taskLabel.trim()} onClick={() => mission.act("装配任务", "/api/task-assembly/assemble", { result_id: candidate.result_id, label: taskLabel })}>装配任务</button>
      </div>}
    </section>
    <section className="mission-section" aria-label="任务装配"><div className="section-heading"><span>任务装配</span><small>{mission.tasks.length}</small></div>
      {!mission.tasks.length && <p className="panel-empty">暂无已装配任务</p>}
      {mission.tasks.map((task) => <TaskRow key={`${task.task_id}-${task.revision}`} task={task} mission={mission} disabled={disabled} />)}
    </section>
    <section className="mission-section" aria-label="技能"><div className="section-heading"><span>Skills</span></div>
      {mission.skills.map((skill) => <div className="mission-list-row" key={skill.id}><span>{skill.name}<small>{skill.id}</small></span><button className="icon-btn" title="执行技能" aria-label="执行技能" disabled={disabled} onClick={() => mission.act("执行技能", `/api/skills/${encodeURIComponent(skill.id)}/execute`, { text: "Review current mission and continue authorized work." })}><Play size={15} /></button></div>)}
      {activeJobs.map((job) => <div className="mission-list-row" key={job.run_id}><span>{job.status}<small>{job.run_id}</small></span><button className="icon-btn" aria-label="取消技能运行" title="取消技能运行" disabled={disabled} onClick={() => mission.act("取消运行", `/api/skills/runs/${encodeURIComponent(job.run_id)}/cancel`)}><Square size={15} /></button></div>)}
    </section>
    {mission.receipt && <details className="command-receipt"><summary>最近操作回执</summary><pre>{JSON.stringify(mission.receipt, null, 2)}</pre></details>}
  </div>;
}

function TaskRow({ task, mission, disabled }) {
  const [label, setLabel] = useState(task.label);
  return <form className="task-row" onSubmit={(event) => { event.preventDefault(); mission.act("更新任务", `/api/task-assembly/tasks/${encodeURIComponent(task.task_id)}`, { label, expected_revision: task.revision }, "patch"); }}>
    <label className="mission-field">任务名称<input value={label} onChange={(event) => setLabel(event.target.value)} disabled={disabled} maxLength={100} /></label>
    <small>{task.status} · REV {task.revision}</small><div className="mission-actions"><button className="primary-action" disabled={disabled || label === task.label || !label.trim()}><Check size={14} />保存</button><button type="button" className="primary-action" onClick={() => mission.preview(task.result_id)}><Eye size={14} />预览</button></div>
  </form>;
}
