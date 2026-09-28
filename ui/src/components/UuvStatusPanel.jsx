import { ChevronRight } from "lucide-react";
import { ownerColor } from "../renderer/colors";
import { uavDisplayState } from "../renderer/displayState";

const TASK_LABELS = {
  idle: "待命",
  coverage: "区域搜索",
  probe: "目标侦察",
  track: "协同跟踪",
  return: "返航",
  holding: "等待降落",
  exiting: "驶离补换",
  acquire: "获取观测",
  reacquiring: "重搜索",
  degraded: "跟踪退化",
};

const STATUS_LABELS = {
  idle: "待命",
  transit: "转场",
  searching: "搜索扫描",
  tracking: "持续跟踪",
  returning: "返航",
  holding: "等待降落",
  failed: "故障停用",
  scanning: "搜索扫描",
  acquiring: "获取观测",
  acquire: "获取观测",
  degraded: "跟踪退化",
  exiting: "驶离补换",
  reacquiring: "重搜索",
  tracking_transit: "跟踪转场",
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
          <h2>UUV 状态</h2>
        </div>
        <b>{active}/{uavs.length} 活动</b>
      </header>

      <section className="summary">
        <div><span>任务区域</span><strong>{frame?.task_area?.width_km || "--"} × {frame?.task_area?.height_km || "--"} km</strong></div>
        <div><span>信息矩阵</span><strong>{frame?.info_matrix?.length || 0} × {frame?.info_matrix?.[0]?.length || 0}</strong></div>
        <div><span>搜索区</span><strong>{frame?.search_regions?.length ?? 0}</strong></div>
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
              key={`${uav.id}-${uav.generation ?? 0}`}
              onClick={() => onSelectUuv?.(selected ? null : uav.id)}
            >
              <i style={{ backgroundColor: ownerColor(uav.id) }} />
              <span>
                <strong>{idLabel(uav.id)} <span className="generation-tag">G{uav.generation ?? "-"}</span></strong>
                <small>{uavDisplayState(uav).label} · {uav.sensor_mode === "passive" ? "被动方位" : uav.sensor_mode === "active" ? "主动扫描" : "传感器待命"}</small>
                <em>{Number.isFinite(uav.energy_pct) ? `${uav.energy_pct.toFixed(0)}% 能量` : "能量 --"} · {Number.isFinite(uav.remaining_range_m) ? `${(uav.remaining_range_m / 1000).toFixed(1)} km` : "航程 --"} · {uav.assigned_region_id || "无搜索区"}</em>
                <span className="energy-track" role="meter" aria-label={`${uav.id} 能量`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={uav.energy_pct ?? 0}><i style={{ width: `${Math.max(0, Math.min(100, uav.energy_pct ?? 0))}%`, backgroundColor: uav.energy_pct < 25 ? "var(--warning)" : "var(--teal)" }} /></span>
              </span>
              <ChevronRight size={14} />
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
                  <div><dt>代次</dt><dd>{uav.generation ?? "--"}</dd></div>
                  <div><dt>能量</dt><dd>{Number.isFinite(uav.energy_pct) ? `${uav.energy_pct.toFixed(1)}%` : "--"}</dd></div>
                  <div><dt>剩余航程</dt><dd>{Number.isFinite(uav.remaining_range_m) ? `${uav.remaining_range_m.toFixed(0)} m` : "--"}</dd></div>
                  <div><dt>速度</dt><dd>{Number.isFinite(uav.speed_mps) ? `${uav.speed_mps.toFixed(1)} m/s` : "--"}</dd></div>
                  <div><dt>航向</dt><dd>{Number.isFinite(uav.heading_deg) ? `${uav.heading_deg.toFixed(1)}°` : "--"}</dd></div>
                  <div><dt>传感器</dt><dd>{uav.sensor_mode || "--"}</dd></div>
                  <div><dt>任务阶段</dt><dd>{uav.task_phase || "--"}</dd></div>
                  <div><dt>搜索区域</dt><dd>{uav.assigned_region_id || "--"}</dd></div>
                  <div><dt>控制权</dt><dd>{uav.control_owner || "--"}</dd></div>
                </dl>
              </>
            );
          })()}
        </div>
      )}

    </aside>
  );
}
