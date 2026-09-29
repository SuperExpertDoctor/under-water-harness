import { Fragment, useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUpRight, Check, CircleX, CornerDownRight, Eye, LoaderCircle, MessageSquareQuote, Send, ShieldAlert, Square, X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { annotationPayload, groupApprovalsByMessage } from "../state/missionState";

const STATUS = { streaming: "生成中", completed: "完成", failed: "失败", cancelled: "已停止", queued: "已排队", delivered: "已送达" };
const TOOL_LABELS = {
  get_observations: "读取观测",
  get_mission_state: "读取任务状态",
  get_action_status: "查询行动状态",
  plan_search: "计算搜索航线",
  plan_tracking: "计算跟踪方案",
  plan_path: "规划路径",
  compute_task_allocation: "计算编队分配",
  evaluate_plan: "校验计划",
  submit_mission_plan: "提交舰队计划",
};
const APPROVAL_MODES = [["request", "请求批准"], ["assisted", "帮我批准"], ["full", "完全访问权限"]];

export default function ConversationPanel({ mission, frame, readOnly, selectedMessageId }) {
  const [text, setText] = useState("");
  const [delivery, setDelivery] = useState("steer");
  const [annotation, setAnnotation] = useState(null);
  const [selection, setSelection] = useState(null);
  const [highlightedMessageId, setHighlightedMessageId] = useState(null);
  const [following, setFollowing] = useState(true);
  const conversation = useRef(null);
  const aligningApproval = useRef(false);
  const composer = useRef(null);
  const messages = (readOnly ? frame?.messages : mission.state.messages) || [];
  const toolsByRun = new Map();
  const lastMessageByRun = new Map();
  for (const message of messages) if (message.role === "assistant" && message.run_id) lastMessageByRun.set(message.run_id, message.id);
  for (const event of (readOnly ? frame?.events : mission.state.events) || []) {
    const data = event.data;
    if (!event.type.startsWith("tool_execution_") || !data?.run_id || !data.tool_call_id) continue;
    if (!toolsByRun.has(data.run_id)) toolsByRun.set(data.run_id, new Map());
    const tools = toolsByRun.get(data.run_id);
    tools.set(data.tool_call_id, { ...tools.get(data.tool_call_id), ...data, status: event.type === "tool_execution_end" ? (data.is_error ? "失败" : "完成") : "执行中", result: data.text });
  }
  const jobs = readOnly ? [] : (mission.state.jobs || []).filter((job) => ["running", "queued"].includes(job.status));
  const approvals = readOnly ? { byMessage: new Map(), unlinked: [] } : groupApprovalsByMessage(messages, mission.state.plans, mission.state.events);
  const pendingCount = [...approvals.byMessage.values(), approvals.unlinked].flat().filter((plan) => plan.status === "pending_approval").length;
  const currentApproval = (readOnly ? [] : mission.state.plans || []).find((plan) => plan.status === "pending_approval");
  const disabled = readOnly || !mission.ready || Boolean(mission.busy);

  function jumpToMessage(messageId) {
    const element = [...(conversation.current?.querySelectorAll("[data-message-id]") || [])].find((item) => item.dataset.messageId === messageId);
    if (!element) return;
    element.scrollIntoView({ block: "center", behavior: "smooth" });
    element.focus({ preventScroll: true });
    setHighlightedMessageId(messageId);
    setFollowing(false);
  }

  useEffect(() => {
    if (disabled) return;
    const captureSelection = () => {
      const selected = window.getSelection();
      if (!selected?.rangeCount || !selected.toString().trim()) return;
      const body = selected.anchorNode?.parentElement?.closest(".agent-message .markdown");
      if (!body || !body.contains(selected.focusNode) || !conversation.current?.contains(body)) return;
      const message = messages.find((item) => item.id === body.closest("[data-message-id]")?.dataset.messageId);
      if (message) setSelection(annotationPayload({ ...message, text: body.textContent }, selected.toString()));
    };
    document.addEventListener("selectionchange", captureSelection);
    return () => document.removeEventListener("selectionchange", captureSelection);
  }, [disabled, messages]);

  useEffect(() => {
    if (!following || !conversation.current) return;
    const pending = conversation.current.querySelector(".approval-request.pending");
    if (pending && pending.clientHeight > conversation.current.clientHeight) {
      aligningApproval.current = true;
      conversation.current.scrollTop = pending.offsetTop - conversation.current.offsetTop - 6;
      const frame = requestAnimationFrame(() => { aligningApproval.current = false; });
      return () => { cancelAnimationFrame(frame); aligningApproval.current = false; };
    } else conversation.current.scrollTop = conversation.current.scrollHeight;
  }, [messages, following, jobs.length, pendingCount]);

  useEffect(() => {
    if (!selectedMessageId || !conversation.current) return;
    const element = [...conversation.current.querySelectorAll("[data-message-id]")].find((item) => item.dataset.messageId === selectedMessageId);
    if (element) {
      conversation.current.scrollTop = element.offsetTop - conversation.current.offsetTop;
      setFollowing(false);
    }
  }, [selectedMessageId]);

  return <section className="conversation-panel" aria-label="PI 对话">
    <div className="agent-conversation" ref={conversation} onScroll={(event) => {
      if (aligningApproval.current) return;
      const node = event.currentTarget;
      setFollowing(node.scrollHeight - node.scrollTop - node.clientHeight < 36);
    }}>
      {!messages.length && <p className="panel-empty">暂无对话</p>}
      {messages.map((message) => {
        const toolOutputs = [...(message.tools || []), ...(lastMessageByRun.get(message.run_id) === message.id ? [...(toolsByRun.get(message.run_id)?.values() || [])] : [])];
        const messageApprovals = approvals.byMessage.get(message.id) || [];
        const hasPendingApproval = messageApprovals.some((plan) => plan.status === "pending_approval");
        const live = message.status === "streaming";
        const content = <>
          {(message.annotations || (message.annotation ? [message.annotation] : [])).map((reference, index) => <div className="annotation-reference" key={`${reference.message_id}-${index}`}>
            <button type="button" className="annotation-link" onClick={() => jumpToMessage(reference.message_id)} title="定位到引用消息"><MessageSquareQuote size={14} />引用消息<ArrowUpRight size={13} /></button>
            <blockquote>{reference.quote}</blockquote>
          </div>)}
          <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: () => null, a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>{message.text || ""}</Markdown></div>
          {live && !hasPendingApproval && <WorkingLine />}
          {!disabled && selection?.message_id === message.id && <button type="button" className="quote-action" onPointerDown={(event) => event.preventDefault()} onClick={() => { setAnnotation(selection); setSelection(null); composer.current?.focus(); }}><MessageSquareQuote size={14} />批注所选内容</button>}
        </>;
        const article = <article className={`agent-message ${message.role} ${selectedMessageId === message.id || highlightedMessageId === message.id ? "linked-message" : ""}`} data-message-id={message.id} tabIndex={-1} onMouseUp={(event) => {
          const selected = window.getSelection();
          const body = event.currentTarget.querySelector(".markdown");
          if (!body || !selected?.rangeCount || !body.contains(selected.anchorNode) || !body.contains(selected.focusNode)) return;
          setSelection(annotationPayload({ ...message, text: body.textContent }, selected.toString()));
        }}>
          {message.role !== "user" && message.role !== "assistant" && <header><strong>工具</strong><span>{STATUS[message.status] || message.status || ""}</span></header>}
          {content}
        </article>;
        if (message.role === "user") return <Fragment key={message.id}>{article}</Fragment>;
        return <div className="agent-turn" key={message.id}>
          {article}
          {(toolOutputs.length > 0 || messageApprovals.length > 0) && <details className="activity-group" open={live || hasPendingApproval ? true : undefined}>
            <summary>{live ? <WorkingLine compact /> : "活动过程"}<span className="activity-count">{toolOutputs.length}</span></summary>
            <ol className="activity-list">
              {toolOutputs.map((tool, index) => {
                const name = tool.tool_name || tool.name || "";
                const waitingApproval = hasPendingApproval && name === "submit_mission_plan" && tool.status !== "失败";
                const state = waitingApproval ? "approval" : tool.status === "执行中" ? "running" : tool.status === "失败" ? "failed" : "done";
                return <li className={`activity-item ${state}`} key={tool.tool_call_id || index}>
                  <details className="tool-output">
                    <summary>
                      <ActivityIcon state={state} />
                      <span className="activity-label">{TOOL_LABELS[name] || name || "工具"}</span>
                      <span className="activity-status">{waitingApproval ? "等待审批" : tool.status || ""}</span>
                    </summary>
                    <pre>{typeof tool.result === "string" ? tool.result : JSON.stringify(tool.result ?? tool, null, 2)}</pre>
                  </details>
                </li>;
              })}
            </ol>
            {messageApprovals.map((plan) => <ApprovalRequestItem key={plan.plan_id} plan={plan} mission={mission} frame={frame} disabled={disabled} />)}
          </details>}
        </div>;
      })}
      {!currentApproval && jobs.map((job) => <div className="working-line" key={job.run_id} title={job.run_id}>
        <LoaderCircle size={14} className="spin" aria-hidden="true" /><span>{job.status === "queued" ? "排队中" : "Working…"}<small>{job.text}</small></span>
        <button className="icon-btn" title="停止生成" aria-label="停止生成" disabled={disabled} onClick={() => mission.act("停止生成", `/api/pi-agent/task-assignment/${encodeURIComponent(job.run_id)}/cancel`)}><Square size={14} /></button>
      </div>)}
      {approvals.unlinked.length > 0 && <div className="unlinked-approvals" role="group" aria-label="未关联到对话回合的审批">
        <span className="unlinked-label">未关联到对话回合</span>
        {approvals.unlinked.map((plan) => <ApprovalRequestItem key={plan.plan_id} plan={plan} mission={mission} frame={frame} disabled={disabled} />)}
      </div>}
    </div>
    {!following && !currentApproval && <button className="follow-latest" onClick={() => setFollowing(true)}><ArrowDown size={14} />最新消息</button>}
    {!readOnly && mission.state.agent?.error && <p className="panel-error" role="alert">{mission.state.agent.error}</p>}
    {currentApproval ? <div className="composer-paused" role="status">等待审批后继续对话</div> : <form className="chat-composer" onSubmit={async (event) => {
      event.preventDefault();
      if (!text.trim() || disabled || currentApproval) return;
      const result = await mission.act("发送消息", "/api/pi-agent/messages", { text: text.trim(), delivery, ...(annotation ? { annotation } : {}) });
      if (result) { setText(""); setAnnotation(null); setFollowing(true); }
    }}>
      {annotation && <div className="annotation-draft"><button type="button" className="annotation-link" title="定位到引用消息" onClick={() => jumpToMessage(annotation.message_id)}><MessageSquareQuote size={14} />引用消息<ArrowUpRight size={13} /></button><blockquote>{annotation.quote}</blockquote><button className="icon-btn" type="button" title="移除批注引用" aria-label="移除批注引用" onClick={() => setAnnotation(null)}><X size={14} /></button></div>}
      <div className="composer-box">
        <textarea ref={composer} id="mission-message" aria-label="任务指令或批注" placeholder={readOnly ? "回放只读" : "发送任务指令或批注…"} value={text} onChange={(event) => setText(event.target.value)} maxLength={4000} rows={3} disabled={disabled || Boolean(currentApproval)} />
        <div className="composer-actions">
          <span className="composer-model" title={(readOnly ? frame?.agent_status?.model : mission.state.agent?.model) || "PI Agent"}>{(readOnly ? frame?.agent_status?.model : mission.state.agent?.model) || "PI Agent"}</span>
          <label className="composer-permission"><select aria-label="审批模式" value={mission.state.autonomy_mode || frame?.autonomy_mode || "request"} disabled={disabled} onChange={(event) => mission.act("切换权限", "/api/permissions/mode", { mode: event.target.value })}>
            {APPROVAL_MODES.map(([mode, label]) => <option key={mode} value={mode}>{label}</option>)}
          </select></label>
          <label className="composer-delivery"><CornerDownRight size={14} /><select aria-label="消息投递方式" value={delivery} onChange={(event) => setDelivery(event.target.value)} disabled={disabled || Boolean(currentApproval)}><option value="steer">当前回合反馈</option><option value="followUp">下一回合跟进</option></select></label>
          <button className="icon-btn send-message" aria-label="发送消息" title="发送消息" disabled={disabled || Boolean(currentApproval) || !text.trim()}><Send size={16} /></button>
        </div>
      </div>
    </form>}
    {mission.error && <p className="panel-error" role="alert">{mission.error}</p>}
  </section>;
}

function WorkingLine({ compact }) {
  return <span className={`working-line ${compact ? "compact" : ""}`} role="status"><LoaderCircle size={14} className="spin" aria-hidden="true" />Working…</span>;
}

function ActivityIcon({ state }) {
  if (state === "running") return <LoaderCircle size={14} className="spin" aria-hidden="true" />;
  if (state === "approval") return <ShieldAlert size={14} aria-hidden="true" />;
  if (state === "failed") return <CircleX size={14} aria-hidden="true" />;
  return <Check size={14} aria-hidden="true" />;
}

function ApprovalRequestItem({ plan, mission, frame, disabled }) {
  const [choice, setChoice] = useState("approve_once");
  const pending = plan.status === "pending_approval";
  const status = pending ? "需要你的批准" : plan.status === "rejected" ? "已拒绝" : plan.status === "expired" ? "已过期" : plan.approval_scope === "approve_session" ? "本会话同类操作已授权" : "仅本次已批准";
  const type = { search: "区域搜索", track: "协同跟踪", reacquire: "重新搜索" }[plan.kind] || "任务计划";
  const reason = { human_required: "请求模式", risk_threshold: "风险达到审批阈值" }[plan.reason] || "审批原因未记录";
  const remaining = Number.isFinite(plan.expires_at_s) ? Math.max(0, Math.ceil(plan.expires_at_s - (frame?.sim_time_min || 0) * 60)) : null;
  return <section className={`approval-request ${pending ? "pending" : "resolved"}`} aria-label={`${type} ${status}`}>
    <div className="approval-request-header"><ShieldAlert size={16} aria-hidden="true" /><strong>{status}</strong><span>{reason}</span></div>
    {pending && <p className="approval-question">是否允许 Agent 执行以下调度？</p>}
    <div className="approval-operation"><strong>{type} · {plan.members?.length ? plan.members.join("、") : "成员未记录"}</strong><span>执行范围（含转场）：{plan.execution_domain?.length === 4 ? `${plan.execution_domain.map((value) => Number(value).toFixed(0)).join("、")} m` : "未记录"}</span></div>
    {plan.decision_reason && <p className="approval-rationale">原因：{plan.decision_reason}</p>}
    <details className="approval-details"><summary>授权范围和计划详情</summary><dl>
      <div><dt>审批原因</dt><dd>{reason}</dd></div>
      <div><dt>会话授权</dt><dd>仅限当前任务、{type}、该范围内{plan.contact_id ? `、接触 ${plan.contact_id}` : ""}且同一策略；切换权限模式后失效</dd></div>
      <div><dt>范围策略</dt><dd>{plan.domain_policy || "未记录"}</dd></div>
      <div><dt>回退策略</dt><dd>{plan.fallback || "未记录"}</dd></div>
      <div><dt>计划 ID</dt><dd>{plan.plan_id}</dd></div>
      <div><dt>风险</dt><dd>{Number.isFinite(plan.risk) ? `${Math.round(plan.risk * 100)}%` : "未记录"}</dd></div>
    </dl></details>
    {pending && <form className="approval-form" onSubmit={(event) => {
      event.preventDefault();
      mission.act("提交审批决定", `/api/approvals/${encodeURIComponent(plan.plan_id)}/decision`, { decision: choice });
    }}>
      <fieldset className="approval-options" disabled={disabled}><legend className="sr-only">审批选项</legend>
        {[["approve_once", "仅允许本次", "只执行当前计划"], ["approve_session", "本会话允许同类操作", "同类型、同范围与同一策略"], ["reject", "拒绝", "当前计划不会执行"]].map(([value, label, detail], index) =>
          <label className={`approval-option ${choice === value ? "selected" : ""}`} key={value}>
            <input type="radio" name={`approval-${plan.plan_id}`} value={value} checked={choice === value} onChange={() => setChoice(value)} />
            <span className="approval-option-number">{index + 1}.</span><span><strong>{label}</strong><small>{detail}</small></span>
          </label>)}
      </fieldset>
      <div className="approval-submit-row"><button type="button" className="approval-preview" onClick={() => mission.preview(plan.plan_id)}><Eye size={14} />地图预览</button><span>{remaining !== null ? `剩余 ${remaining} s` : "等待你的决定"}</span><button className="approval-submit" type="submit" disabled={disabled}>提交<CornerDownRight size={14} /></button></div>
    </form>}
    {pending && <small className="approval-paused">新计划等待审批；已授权任务继续执行</small>}
  </section>;
}
