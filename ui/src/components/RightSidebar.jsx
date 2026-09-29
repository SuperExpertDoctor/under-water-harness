import { useEffect, useState } from "react";
import { Bot, CircleX, Crosshair, MousePointer2, Radio, RadioTower, Radar, Ship, Trash2, Waypoints } from "lucide-react";
import {
  controlOwnerDisplayLabel,
  taskTypeDisplayLabel,
  uavDisplayState,
  vehicleDisplayId,
} from "../renderer/displayState";
import { informationCellCounts } from "../state/missionState";
import ContactPanel from "./ContactPanel";
import CoveragePanel from "./CoveragePanel";
import IntentPanel from "./IntentPanel";
import ConversationPanel from "./ConversationPanel";
import UuvStatusPanel from "./UuvStatusPanel";

const STATUS_LABELS = {
  idle: "待命",
  transit: "转场",
  searching: "搜索",
  tracking: "跟踪",
  returning: "返航",
};

const VESSEL_CLASS_LABELS = {
  type_i: "I 类",
  type_ii: "II 类",
  unknown: "未知",
};

const SURVEILLANCE_STAGE_LABELS = {
  undetected: "未发现",
  detected: "已发现",
  probing: "递进侦察",
  tracking: "持续跟踪",
};

export default function RightSidebar({
  frame,
  onSelectUav,
  selectedUavId,
  open,
  onClose,
  lastLlmCycle,
  readOnly,
  connectionStatus,
  selection,
  onClearSelection,
  selectedContactId,
  onSelectContact,
  editingAllowed,
  vesselPlacement,
  onSelectVesselType,
  onCancelVesselPlacement,
  selectedScenarioVesselId,
  onSelectScenarioVessel,
  onDeleteVessel,
  onSetVesselAis,
  vesselCommandStatus,
  mission,
  selectedMessageId,
  sceneVisible,
  onToggleScene,
}) {
  const [tab, setTab] = useState("chat");
  const pendingCount = readOnly ? 0 : mission?.state.plans?.filter((plan) => plan.status === "pending_approval").length || 0;
  useEffect(() => { if (selection) setTab("state"); }, [selection]);
  useEffect(() => { if (selectedMessageId) setTab("chat"); }, [selectedMessageId]);
  useEffect(() => { if (pendingCount) setTab("chat"); }, [pendingCount]);
  const uavs = frame?.uavs || [];
  const ships = frame?.ships || [];
  const contacts = frame?.contacts || [];
  const regions = frame?.search_regions || [];
  const tracks = frame?.track_regions || [];
  const info = frame?.info_matrix || [];
  let scanned = 0;
  let total = 0;
  info.forEach((column) => column.forEach((value) => {
    total += 1;
    if (value > 0) scanned += 1;
  }));
  const situations = informationCellCounts(frame);
  const coverage = Number.isFinite(frame?.coverage_pct)
    ? frame.coverage_pct
    : (total ? scanned / total * 100 : 0);
  const fallbackTotal = Math.round(
    Number(frame?.task_area?.width_km || 0)
      / Number(frame?.task_area?.cell_size_km || 0)
      * Number(frame?.task_area?.height_km || 0)
      / Number(frame?.task_area?.cell_size_km || 0),
  ) || total || 0;
  const selected = uavs.find((uav) => uav.id === selectedUavId);
  const scenarioVessels = frame?.scenario_vessels || [];
  const selectedScenarioVessel = scenarioVessels.find(
    (vessel) => vessel.scenario_entity_id === selectedScenarioVesselId,
  );
  const vesselCommandBusy = vesselCommandStatus?.status === "queued";
  const canEditVessels = Boolean(editingAllowed) && !vesselCommandBusy;

  return (
    <aside className={`sidebar ${open ? "open" : ""} ${tab === "chat" ? "conversation-sidebar" : ""}`} aria-label="任务工作区">
      <div className="sidebar-header">
        <strong className="sidebar-title">{tab === "chat" ? <>PI Agent <small className="sidebar-agent-status">{readOnly ? "只读" : { idle: "待命", running: "运行中", offline: "离线", degraded: "异常", queued: "排队", unavailable: "不可用" }[mission?.state.agent?.status] || "待命"}</small></> : "任务工作区"}</strong>
        <button className="icon-btn mobile-only" onClick={onClose} aria-label="关闭编队状态" title="关闭"><CircleX size={17} /></button>
      </div>
      <div className="sidebar-tabs" role="tablist" aria-label="任务视图">
        {[["chat", "对话"], ["state", "数据"]].map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}{id === "chat" && pendingCount > 0 && <span className="sidebar-tab-count">{pendingCount}</span>}</button>)}
      </div>
      {tab === "chat" && mission ? (
        <ConversationPanel key={frame?.episode_id} mission={mission} frame={frame} readOnly={readOnly} selectedMessageId={selectedMessageId} />
      ) : !frame ? (
        <div className="sidebar-empty"><Radar size={24} /><span>等待任务数据</span></div>
      ) : (
        <>
          <UuvStatusPanel frame={frame} selectedUuvId={selectedUavId} onSelectUuv={onSelectUav} />
          <section className="sidebar-section overview-grid" aria-label="任务概览">
            <Metric label="仿真时间" value={frame.timestamp || "--:--:--"} />
            <Metric label="决策周期" value={`#${frame.cycle ?? 0}`} />
            <Metric label="累计观测覆盖" value={`${coverage.toFixed(1)}%`} emphasized title="基于历史观测数据统计" />
            <Metric label="观测接触" value={contacts.length || ships.filter((ship) => ship.is_detected).length} />
          </section>

          <CoveragePanel frame={frame} connectionStatus={connectionStatus} readOnly={readOnly} />

          {editingAllowed && <details className="sidebar-section vessel-editor" open={sceneVisible} onToggle={(event) => { if (event.currentTarget.open !== sceneVisible) onToggleScene?.(event.currentTarget.open); }} aria-label="初始化船舶编辑">
            <summary>场景编辑 · 调试真值</summary>
            <div className="section-heading">
              <span><Ship size={15} />场景船舶</span>
              <small>{frame.actual_vessel_count ?? ships.length}/{frame.initial_vessel_count ?? ships.length}</small>
            </div>
            <div className="vessel-palette" role="group" aria-label="船舶组件库">
              <button
                type="button"
                className={vesselPlacement === "type_i" ? "vessel-tool active" : "vessel-tool"}
                aria-pressed={vesselPlacement === "type_i"}
                aria-label="I 类船舶"
                draggable={canEditVessels}
                disabled={!canEditVessels}
                onClick={() => onSelectVesselType?.(vesselPlacement === "type_i" ? null : "type_i")}
                onDragStart={(event) => {
                  event.dataTransfer.setData("application/x-vessel-class", "type_i");
                  onSelectVesselType?.("type_i");
                }}
                title="选择后点击地图放置 I 类船舶"
              >
                <Ship size={17} /><span>I 类船舶</span>
              </button>
              <button
                type="button"
                className={vesselPlacement === "type_ii" ? "vessel-tool active" : "vessel-tool"}
                aria-pressed={vesselPlacement === "type_ii"}
                aria-label="II 类船舶"
                draggable={canEditVessels}
                disabled={!canEditVessels}
                onClick={() => onSelectVesselType?.(vesselPlacement === "type_ii" ? null : "type_ii")}
                onDragStart={(event) => {
                  event.dataTransfer.setData("application/x-vessel-class", "type_ii");
                  onSelectVesselType?.("type_ii");
                }}
                title="选择后点击地图放置 II 类船舶"
              >
                <Radar size={17} /><span>II 类船舶</span>
              </button>
            </div>
            {vesselPlacement && canEditVessels && (
              <div className="placement-status">
                <MousePointer2 size={14} />
                <span>地图上选择位置</span>
                <button type="button" className="compact-icon icon-btn" onClick={onCancelVesselPlacement} aria-label="取消放置船舶" title="取消放置船舶"><CircleX size={14} /></button>
              </div>
            )}
            {scenarioVessels.length > 0 && (
              <div className="scenario-vessel-list" aria-label="场景船舶列表">
                {scenarioVessels.map((vessel) => (
                  <button
                    type="button"
                    key={vessel.scenario_entity_id}
                    className={selectedScenarioVesselId === vessel.scenario_entity_id ? "scenario-vessel-row selected" : "scenario-vessel-row"}
                    aria-pressed={selectedScenarioVesselId === vessel.scenario_entity_id}
                    onClick={() => onSelectScenarioVessel?.(
                      selectedScenarioVesselId === vessel.scenario_entity_id ? null : vessel.scenario_entity_id,
                    )}
                  >
                    <span><Ship size={13} />{vessel.scenario_entity_id}</span>
                    <small>{VESSEL_CLASS_LABELS[vessel.vessel_class] || "未知"} · {vessel.ais_enabled ? "AIS 开启" : "AIS 关闭"}</small>
                  </button>
                ))}
              </div>
            )}
            {selectedScenarioVessel && canEditVessels && (
              <button type="button" className="delete-vessel-action" onClick={onDeleteVessel} aria-label="删除选中船舶">
                <Trash2 size={14} />删除选中船舶
              </button>
            )}
            {!editingAllowed && <p className="editor-note">当前场景只读；回放和已结束任务不可编辑</p>}
            {vesselCommandBusy && <p className="editor-note">命令处理中，等待权威 frame 更新</p>}
            {vesselCommandStatus && (
              <div className={`vessel-command-status ${vesselCommandStatus.status}`} role="status">
                <span>{vesselCommandStatus.message}</span>
                {vesselCommandStatus.errorCode && <small>{vesselCommandStatus.errorCode}</small>}
              </div>
            )}
          </details>}

          {selectedScenarioVessel && (
            <section className="sidebar-section selected-vessel-detail" aria-label="选中船舶详情">
              <div className="section-heading">
                <span><Ship size={15} />{selectedScenarioVessel.scenario_entity_id}</span>
                <small>REV {selectedScenarioVessel.revision}</small>
              </div>
              <dl className="vessel-detail-grid">
                <div><dt>类别</dt><dd>{VESSEL_CLASS_LABELS[selectedScenarioVessel.vessel_class] || "未知"}</dd></div>
                <div><dt>侦察阶段</dt><dd>{SURVEILLANCE_STAGE_LABELS[selectedScenarioVessel.surveillance_stage] || "未发现"}</dd></div>
                <div><dt>位置</dt><dd className="mono">{selectedScenarioVessel.position?.map((value) => Number(value).toFixed(1)).join(", ") || "-"}</dd></div>
                <div><dt>AIS</dt><dd>{selectedScenarioVessel.ais_enabled ? "开启" : "关闭"}</dd></div>
              </dl>
              <div className="ais-control" role="group" aria-label="AIS 开关">
                <button
                  type="button"
                  className={selectedScenarioVessel.ais_enabled ? "active" : ""}
                  aria-label="开启 AIS"
                  aria-pressed={selectedScenarioVessel.ais_enabled}
                  disabled={!selectedScenarioVessel.ais_controllable || !canEditVessels || selectedScenarioVessel.ais_enabled}
                  onClick={() => onSetVesselAis?.(true)}
                >
                  <RadioTower size={14} />开启
                </button>
                <button
                  type="button"
                  className={!selectedScenarioVessel.ais_enabled ? "active" : ""}
                  aria-label="关闭 AIS"
                  aria-pressed={!selectedScenarioVessel.ais_enabled}
                  disabled={!selectedScenarioVessel.ais_controllable || !canEditVessels || !selectedScenarioVessel.ais_enabled}
                  onClick={() => onSetVesselAis?.(false)}
                >
                  <Radio size={14} />关闭
                </button>
              </div>
              {!selectedScenarioVessel.ais_controllable && <p className="editor-note">I 类船舶 AIS 固定开启</p>}
            </section>
          )}

          <section className="sidebar-section">
            <div className="section-heading">
              <span>信息态势</span>
              <small>
                {frame.searchable_cells || fallbackTotal} CELLS ·
                {frame.information_source === "backend" ? "BACKEND" : "LOCAL"} V{frame.__information?.version ?? frame.information_version ?? 0}
              </small>
            </div>
            <div className="situation-strip">
              <Situation label="白" value={situations.white} tone="white" />
              <Situation label="灰" value={situations.gray} tone="gray" />
              <Situation label="黑" value={situations.black || (total ? 0 : fallbackTotal)} tone="black" />
            </div>
            <div className="coverage-track"><i style={{ width: `${coverage}%` }} /></div>
          </section>

          {selected && (
            <section className="sidebar-section selected-detail">
              <div className="section-heading"><span>{vehicleDisplayId(selected.id)} 详情</span></div>
              <dl>
                <div><dt>任务模式</dt><dd>{taskTypeDisplayLabel(selected)}</dd></div>
                <div><dt>运行状态</dt><dd>{uavDisplayState(selected).label}</dd></div>
                <div><dt>控制权</dt><dd>{controlOwnerDisplayLabel(selected)}</dd></div>
                <div><dt>坐标</dt><dd>{selected.position?.map((value) => Number(value).toFixed(1)).join(", ") || "-"}</dd></div>
                <div><dt>搜索区域</dt><dd>{selected.assigned_region_id || "-"}</dd></div>
                <div><dt>目标群</dt><dd>{selected.target_group_id || "-"}</dd></div>
              </dl>
            </section>
          )}

          <section className="sidebar-section compact-stats">
            <div><Waypoints size={15} /><span>搜索区</span><b>{regions.length}</b></div>
            <div><Crosshair size={15} /><span>跟踪区</span><b>{tracks.length}</b></div>
            <div><Ship size={15} /><span>标记点</span><b>{frame.markers?.length || 0}</b></div>
          </section>

          <IntentPanel
            key={frame.episode_id}
            frame={frame}
            readOnly={readOnly}
            selection={selection}
            onClearSelection={onClearSelection}
          />
          <ContactPanel
            frame={frame}
            selectedContactId={selectedContactId}
            onSelectContact={onSelectContact}
          />

          <section className="sidebar-section llm-summary">
            <div className="section-heading"><span><Bot size={15} />模型决策</span><small>{lastLlmCycle?.model || mission?.state.agent?.model || "未连接"}</small></div>
            {lastLlmCycle ? (
              <div className="llm-status-row">
                <span className={lastLlmCycle.success ? "success" : "failed"}>{lastLlmCycle.success ? "校验通过" : "决策失败"}</span>
                <b>{lastLlmCycle.attempts?.length || 0} 次请求</b>
              </div>
            ) : <p>等待首次重量触发</p>}
          </section>
        </>
      )}
    </aside>
  );
}

function Metric({ label, value, emphasized, title }) {
  return <div className={emphasized ? "metric emphasized" : "metric"} title={title}><span>{label}</span><strong>{value}</strong></div>;
}

function Situation({ label, value, tone }) {
  return <div className={`situation ${tone}`}><span>{label}态</span><strong>{value}</strong></div>;
}
