import { STATUS_COLORS, UUV_SCAN_RADIUS_CELLS } from "../renderer/telemetryRenderer";

const TASK_LABELS = {
  idle: "待命",
  coverage: "区域搜索",
  probe: "目标侦察",
  track: "协同跟踪",
  return: "返航",
  holding: "等待降落",
};

const STATUS_LABELS = {
  idle: "待命",
  transit: "转场",
  searching: "搜索扫描",
  tracking: "持续跟踪",
  returning: "返航",
  holding: "等待降落",
  failed: "故障停用",
};

function idLabel(id) {
  return String(id || "").replace(/^UAV-/i, "UUV-");
}

function taskLabel(uav) {
  const value = uav?.task_visual?.task_type || uav?.operation_mode || uav?.status || "idle";
  return TASK_LABELS[value] || value;
}

function statusLabel(uav) {
  return STATUS_LABELS[uav?.status] || uav?.status || "未知";
}

export default function UuvStatusPanel({ frame, selectedUuvId, onSelectUuv }) {
  const uavs = frame?.uavs || [];
  const active = uavs.filter((uav) => uav.status !== "idle" && uav.operational_status !== "failed").length;
  return (
    <aside className="status-panel">
      <header className="panel-title">
        <div>
          <span>LIVE TELEMETRY</span>
          <h1>UUV 编队状态</h1>
        </div>
        <b>{active}/{uavs.length} 活动</b>
      </header>

      <section className="summary">
        <div><span>任务区域</span><strong>{frame?.task_area?.width_km || "--"} × {frame?.task_area?.height_km || "--"} km</strong></div>
        <div><span>信息矩阵</span><strong>{frame?.info_matrix?.length || 0} × {frame?.info_matrix?.[0]?.length || 0}</strong></div>
        <div><span>扫描半径</span><strong>R = {UUV_SCAN_RADIUS_CELLS} 格</strong></div>
        <div><span>最新帧</span><strong>#{frame?.frame_id ?? "--"}</strong></div>
      </section>

      <div className="uuv-list">
        {uavs.map((uav) => {
          const selected = uav.id === selectedUuvId;
          const position = Array.isArray(uav.position)
            ? `${Number(uav.position[0]).toFixed(1)}, ${Number(uav.position[1]).toFixed(1)}`
            : "--";
          return (
            <button
              type="button"
              className={`uuv-row ${selected ? "selected" : ""}`}
              key={uav.id}
              onClick={() => onSelectUuv?.(selected ? null : uav.id)}
            >
              <i style={{ backgroundColor: STATUS_COLORS[uav.status] || "#64748b" }} />
              <span>
                <strong>{idLabel(uav.id)}</strong>
                <small>{taskLabel(uav)} · {statusLabel(uav)}</small>
                <em>位置 {position} · 轨迹 {uav.trail?.length || 0} 点</em>
              </span>
              <b>›</b>
            </button>
          );
        })}
        {!uavs.length && <div className="empty-list">等待后端发送 UUV 数据</div>}
      </div>

      {selectedUuvId && (
        <div className="selected-card">
          {(() => {
            const uav = uavs.find((item) => item.id === selectedUuvId);
            if (!uav) return null;
            return (
              <>
                <div className="selected-heading">
                  <strong>{idLabel(uav.id)}</strong>
                  <span>{statusLabel(uav)}</span>
                </div>
                <dl>
                  <div><dt>任务模式</dt><dd>{taskLabel(uav)}</dd></div>
                  <div><dt>运行状态</dt><dd>{statusLabel(uav)}</dd></div>
                  <div><dt>当前位置</dt><dd>{uav.position?.map((value) => Number(value).toFixed(2)).join(", ") || "--"}</dd></div>
                  <div><dt>轨迹点数</dt><dd>{uav.trail?.length || 0}</dd></div>
                </dl>
              </>
            );
          })()}
        </div>
      )}

      <footer className="panel-note">位置、轨迹和信息量均来自后端最新帧。UI 不重新计算衰减和任务算法。</footer>
    </aside>
  );
}
