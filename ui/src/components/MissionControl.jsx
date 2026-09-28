import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, Hand, Pause, Play, RotateCcw, ShieldAlert, ShieldCheck, Square, X } from "lucide-react";

const SIM_STATUS = { running: "运行", paused: "暂停", stopped: "已停止", ready: "就绪", local_demo: "离线演示", safety_paused: "安全暂停" };
const AGENT_STATUS = { idle: "待命", running: "决策中", offline: "离线", degraded: "异常", queued: "排队", unavailable: "不可用" };
const APPROVAL_MODES = [
  { mode: "request", label: "请求批准", description: "每项 UUV 任务计划都由你批准", Icon: Hand },
  { mode: "assisted", label: "帮我批准", description: "低风险计划自动执行，高风险计划请求批准", Icon: ShieldCheck },
  { mode: "full", label: "完全访问权限", description: "有效的 UUV 任务计划自动执行；不开放命令、文件或联网权限", Icon: ShieldAlert },
];

export default function MissionControl({ mission, frame, readOnly, recordingStatus, recordingState }) {
  const [modeMenuOpen, setModeMenuOpen] = useState(false);
  const modePickerRef = useRef(null);
  const modeTriggerRef = useRef(null);
  useEffect(() => {
    if (!modeMenuOpen) return undefined;
    const closeOnOutside = (event) => {
      if (!modePickerRef.current?.contains(event.target)) setModeMenuOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === "Escape") {
        setModeMenuOpen(false);
        modeTriggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [modeMenuOpen]);
  const disabled = readOnly || !mission.ready || Boolean(mission.busy);
  const status = frame?.runtime_status;
  const agent = mission.state.agent || frame?.agent_status || {};
  const selectedMode = APPROVAL_MODES.find(({ mode }) => mode === (mission.state.autonomy_mode || frame?.autonomy_mode)) || APPROVAL_MODES[0];
  return <section className="mission-control" aria-label="任务控制">
    <div className="simulation-controls" role="group" aria-label="仿真操作">
      {[["start", "启动仿真", Play], ["pause", "暂停仿真", Pause], ["stop", "停止任务", Square], ["reset", "重置回合", RotateCcw]].map(([action, label, Icon]) =>
        <button key={action} className={`icon-btn ${action === "stop" ? "danger-action" : ""}`} title={label} aria-label={label}
          disabled={(action === "stop" ? readOnly || !mission.ready : disabled) || (action === "start" && ["running", "stopped"].includes(status)) || (action === "pause" && status !== "running")}
          onClick={() => {
            if (action === "reset" && !window.confirm("重置当前回合？当前任务和审批将失效。")) return;
            mission.act(label, `/api/simulation/${action}`);
          }}><Icon size={17} /></button>)}
    </div>
    <div className="autonomy-control" ref={modePickerRef}>
      <button ref={modeTriggerRef} type="button" className="approval-mode-trigger" aria-label={`审批模式：${selectedMode.label}`}
        aria-expanded={modeMenuOpen} aria-haspopup="dialog" aria-controls="approval-mode-menu" disabled={disabled}
        onClick={() => setModeMenuOpen((open) => !open)}>
        <selectedMode.Icon size={15} aria-hidden="true" /><span>审批模式</span><strong>{selectedMode.label}</strong><ChevronDown size={14} aria-hidden="true" />
      </button>
      {modeMenuOpen && <div id="approval-mode-menu" className="approval-mode-menu" role="dialog" aria-label="选择审批模式">
        <div className="approval-mode-heading">如何批准 Agent 的 UUV 任务计划？</div>
        <div role="radiogroup" aria-label="自动审批等级">
          {APPROVAL_MODES.map(({ mode, label, description, Icon }) =>
            <button key={mode} type="button" role="radio" aria-checked={selectedMode.mode === mode}
              className={`approval-mode-option ${selectedMode.mode === mode ? "selected" : ""}`} disabled={disabled}
              onClick={() => {
                setModeMenuOpen(false);
                modeTriggerRef.current?.focus();
                if (selectedMode.mode !== mode) mission.act("切换权限", "/api/permissions/mode", { mode });
              }}>
              <Icon size={18} aria-hidden="true" /><span><strong>{label}</strong><small>{description}</small></span>
              {selectedMode.mode === mode && <Check size={16} aria-hidden="true" />}
            </button>)}
        </div>
        <p className="approval-mode-scope">仅控制本游戏任务计划的审批。</p>
      </div>}
    </div>
    <div className="mission-statuses" aria-live="polite">
      <span>仿真 <b>{status === "local_demo" ? "离线演示" : readOnly ? "回放只读" : SIM_STATUS[status] || status || "未连接"}</b></span>
      <span title={agent.error || ""}>PI <b className={agent.error ? "failed" : ""}>{AGENT_STATUS[agent.status] || agent.status || "未连接"}</b></span>
      {recordingStatus && <span className={`recording-status ${recordingState}`} role={["failed", "unavailable"].includes(recordingState) ? "alert" : "status"} title={recordingStatus}>{recordingStatus}</span>}
      {mission.busy && <span role="status">{mission.busy}…</span>}
    </div>
    {mission.error && <div className="mission-error" role="alert"><span>{mission.error}</span><button className="icon-btn" onClick={mission.clearError} title="关闭错误" aria-label="关闭错误"><X size={14} /></button></div>}
  </section>;
}
