import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUpRight, Check, CornerDownRight, Eye, MessageSquareQuote, Send, ShieldAlert, Square, X } from "lucide-react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { annotationPayload } from "../state/missionState";

const STATUS = { streaming: "生成中", completed: "完成", failed: "失败", cancelled: "已停止", queued: "已排队", delivered: "已送达" };

export default function ConversationPanel({ mission, frame, readOnly, selectedMessageId }) {
  const [text, setText] = useState("");
  const [delivery, setDelivery] = useState("steer");
  const [annotation, setAnnotation] = useState(null);
  const [selection, setSelection] = useState(null);
  const [highlightedMessageId, setHighlightedMessageId] = useState(null);
  const [following, setFollowing] = useState(true);
  const conversation = useRef(null);
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
  const pending = readOnly ? [] : (mission.state.plans || []).filter((plan) => plan.status === "pending_approval");
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
    if (following && conversation.current) conversation.current.scrollTop = conversation.current.scrollHeight;
  }, [messages, following, jobs.length, pending.length]);

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
      const node = event.currentTarget;
      setFollowing(node.scrollHeight - node.scrollTop - node.clientHeight < 36);
    }}>
      {!messages.length && <p className="panel-empty">暂无对话</p>}
      {messages.map((message) => {
        const toolOutputs = [...(message.tools || []), ...(lastMessageByRun.get(message.run_id) === message.id ? [...(toolsByRun.get(message.run_id)?.values() || [])] : [])];
        return <article className={`agent-message ${message.role} ${selectedMessageId === message.id || highlightedMessageId === message.id ? "linked-message" : ""}`} key={message.id} data-message-id={message.id} tabIndex={-1} onMouseUp={(event) => {
        const selected = window.getSelection();
        const body = event.currentTarget.querySelector(".markdown");
        if (!body || !selected?.rangeCount || !body.contains(selected.anchorNode) || !body.contains(selected.focusNode)) return;
        setSelection(annotationPayload({ ...message, text: body.textContent }, selected.toString()));
      }}>
        <header><strong>{message.role === "user" ? "你" : message.role === "tool" ? "工具" : "PI"}</strong><span>{message.model || ""} {STATUS[message.status] || message.status || ""}</span></header>
        {(message.annotations || (message.annotation ? [message.annotation] : [])).map((reference, index) => <div className="annotation-reference" key={`${reference.message_id}-${index}`}>
          <button type="button" className="annotation-link" onClick={() => jumpToMessage(reference.message_id)} title="定位到引用消息"><MessageSquareQuote size={14} />引用消息<ArrowUpRight size={13} /></button>
          <blockquote>{reference.quote}</blockquote>
        </div>)}
        <div className="markdown"><Markdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: () => null, a: ({ children, href }) => <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>{message.text || ""}</Markdown></div>
        {message.status === "streaming" && <span className="stream-status" role="status">生成中</span>}
        {toolOutputs.length > 0 && <details className="tool-group"><summary>工具调用 <span>{toolOutputs.length}</span></summary>
          {toolOutputs.map((tool, index) => <details className="tool-output" key={tool.tool_call_id || index}><summary>{tool.tool_name || tool.name || "工具"} · {tool.status || ""}</summary><pre>{typeof tool.result === "string" ? tool.result : JSON.stringify(tool.result ?? tool, null, 2)}</pre></details>)}
        </details>}
        {!disabled && selection?.message_id === message.id && <button type="button" className="quote-action" onPointerDown={(event) => event.preventDefault()} onClick={() => { setAnnotation(selection); setSelection(null); composer.current?.focus(); }}><MessageSquareQuote size={14} />批注所选内容</button>}
      </article>})}
      {jobs.map((job) => <div className="generation-row" key={job.run_id}><span>{job.status === "queued" ? "等待生成" : "正在生成"}<small>{job.run_id}</small></span><button className="icon-btn" title="停止生成" aria-label="停止生成" disabled={disabled} onClick={() => mission.act("停止生成", `/api/pi-agent/task-assignment/${encodeURIComponent(job.run_id)}/cancel`)}><Square size={14} /></button></div>)}
      {pending.length > 0 && <div className="conversation-approvals" role="region" aria-label="待批准计划" aria-live="polite">
        <div className="conversation-approval-heading"><ShieldAlert size={16} /><strong>待批准计划</strong><span>{pending.length}</span></div>
        {pending.map((plan) => <div className="conversation-approval" key={plan.plan_id}>
          <div className="approval-description"><strong>{plan.kind === "search" ? "区域搜索" : plan.kind === "track" ? "协同跟踪" : "任务计划"}</strong><span>{plan.members?.join(" · ") || "无成员"}</span></div>
          <div className="approval-facts"><code>{plan.plan_id}</code><span>风险 {Number.isFinite(plan.risk) ? `${Math.round(plan.risk * 100)}%` : "--"}</span><span>剩余 {Math.max(0, Math.ceil(plan.expires_at_s - (frame?.sim_time_min || 0) * 60))} s</span></div>
          <div className="approval-inline-actions">
            <button type="button" className="icon-btn" title="预览计划" aria-label={`预览 ${plan.plan_id}`} onClick={() => mission.preview(plan.plan_id)}><Eye size={16} /></button>
            <button type="button" disabled={disabled} onClick={() => mission.act("拒绝计划", `/api/approvals/${encodeURIComponent(plan.plan_id)}/decision`, { decision: "reject" })}><X size={15} />拒绝</button>
            <button type="button" className="approve-command" disabled={disabled} onClick={() => mission.act("批准计划", `/api/approvals/${encodeURIComponent(plan.plan_id)}/decision`, { decision: "approve" })}><Check size={15} />批准</button>
          </div>
        </div>)}
      </div>}
    </div>
    {!following && <button className="follow-latest" onClick={() => setFollowing(true)}><ArrowDown size={14} />最新消息</button>}
    {!readOnly && mission.state.agent?.error && <p className="panel-error" role="alert">{mission.state.agent.error}</p>}
    <form className="chat-composer" onSubmit={async (event) => {
      event.preventDefault();
      if (!text.trim() || disabled) return;
      const result = await mission.act("发送消息", "/api/pi-agent/messages", { text: text.trim(), delivery, ...(annotation ? { annotation } : {}) });
      if (result) { setText(""); setAnnotation(null); setFollowing(true); }
    }}>
      {annotation && <div className="annotation-draft"><button type="button" className="annotation-link" title="定位到引用消息" onClick={() => jumpToMessage(annotation.message_id)}><MessageSquareQuote size={14} />引用消息<ArrowUpRight size={13} /></button><blockquote>{annotation.quote}</blockquote><button className="icon-btn" type="button" title="移除批注引用" aria-label="移除批注引用" onClick={() => setAnnotation(null)}><X size={14} /></button></div>}
      <label className="composer-label" htmlFor="mission-message">任务指令或批注</label>
      <textarea ref={composer} id="mission-message" placeholder={readOnly ? "回放只读" : "发送任务指令或批注"} value={text} onChange={(event) => setText(event.target.value)} maxLength={4000} rows={3} disabled={disabled} />
      <div className="composer-actions"><span className="composer-model" title={(readOnly ? frame?.agent_status?.model : mission.state.agent?.model) || "PI Agent"}>{(readOnly ? frame?.agent_status?.model : mission.state.agent?.model) || "PI Agent"}</span><label><CornerDownRight size={14} /><select aria-label="消息投递方式" value={delivery} onChange={(event) => setDelivery(event.target.value)} disabled={disabled}><option value="steer">当前回合反馈</option><option value="followUp">下一回合跟进</option></select></label><button className="icon-btn send-message" aria-label="发送消息" title="发送消息" disabled={disabled || !text.trim()}><Send size={16} /></button></div>
    </form>
  </section>;
}
