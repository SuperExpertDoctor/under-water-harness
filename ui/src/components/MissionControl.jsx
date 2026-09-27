import { Pause, Play, RotateCcw, Square, X } from "lucide-react";

const SIM_STATUS = { running: "运行", paused: "暂停", stopped: "已停止", ready: "就绪", local_demo: "离线演示", safety_paused: "安全暂停" };
const AGENT_STATUS = { idle: "待命", running: "决策中", offline: "离线", degraded: "异常", queued: "排队", unavailable: "不可用" };

export default function MissionControl({ mission, frame, readOnly }) {
  const disabled = readOnly || !mission.ready || Boolean(mission.busy);
  const status = frame?.runtime_status;
  const agent = mission.state.agent || frame?.agent_status || {};
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
    <div className="autonomy-control" role="group" aria-label="自主权限模式">
      {[["request", "请求"], ["assisted", "辅助"], ["full", "全自主"]].map(([mode, label]) =>
        <button key={mode} disabled={disabled} aria-pressed={(mission.state.autonomy_mode || frame?.autonomy_mode) === mode}
          className={(mission.state.autonomy_mode || frame?.autonomy_mode) === mode ? "active" : ""}
          onClick={() => mission.act("切换权限", "/api/permissions/mode", { mode })}>{label}</button>)}
    </div>
    <div className="mission-statuses" aria-live="polite">
      <span>仿真 <b>{status === "local_demo" ? "离线演示" : readOnly ? "回放只读" : SIM_STATUS[status] || status || "未连接"}</b></span>
      <span title={agent.error || ""}>PI <b className={agent.error ? "failed" : ""}>{AGENT_STATUS[agent.status] || agent.status || "未连接"}</b></span>
      {mission.busy && <span role="status">{mission.busy}…</span>}
    </div>
    {mission.error && <div className="mission-error" role="alert"><span>{mission.error}</span><button className="icon-btn" onClick={mission.clearError} title="关闭错误" aria-label="关闭错误"><X size={14} /></button></div>}
  </section>;
}
