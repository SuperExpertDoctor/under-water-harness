import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, Focus, Grid3X3, History, PanelBottom, PanelRight, Radio, Route, Square, Wind, Workflow } from "lucide-react";

import BottomDrawer from "./components/BottomDrawer";
import CanvasMap from "./components/CanvasMap";
import PlaybackBar from "./components/PlaybackBar";
import PluginPanel from "./components/PluginPanel";
import RightSidebar from "./components/RightSidebar";
import MissionControl from "./components/MissionControl";
import useMissionControl from "./hooks/useMissionControl";
import useRecording, { recordingPresentation } from "./hooks/useRecording";
import useReplay from "./hooks/useReplay";
import useMp4Export from "./hooks/useMp4Export";
import useWebSocket from "./hooks/useWebSocket";
import { DEMO_FRAME } from "./state/demoFrame";
import { applyInformationField } from "./state/informationField";


export default function App() {
  const [mode, setMode] = useState("live");
  const [pluginView, setPluginView] = useState(false);
  const [selectedUavId, setSelectedUavId] = useState(null);
  const [drawerVisible, setDrawerVisible] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(
    () => !globalThis.matchMedia || globalThis.matchMedia("(min-width: 901px)").matches,
  );
  const [showGrid, setShowGrid] = useState(false);
  const [showScenario, setShowScenario] = useState(false);
  const [trailMode, setTrailMode] = useState("tail");
  const [mapMode, setMapMode] = useState("situation");
  const [selectedPlanId, setSelectedPlanId] = useState(null);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedBBox, setSelectedBBox] = useState(null);
  const [selectedContactId, setSelectedContactId] = useState(null);
  const [vesselPlacement, setVesselPlacement] = useState(null);
  const [selectedScenarioVesselId, setSelectedScenarioVesselId] = useState(null);
  const [vesselCommandStatus, setVesselCommandStatus] = useState(null);
  const [liveEvents, setLiveEvents] = useState([]);
  const [lastLlmCycle, setLastLlmCycle] = useState(null);
  const [scene, setScene] = useState(null);
  const [sceneError, setSceneError] = useState("");
  const [selectedMessageId, setSelectedMessageId] = useState(null);
  const mapExporterRef = useRef(null);
  const mission = useMissionControl(mode === "live");
  const recording = useRecording();
  const recordingView = recordingPresentation(recording.status, recording.error);
  const live = useWebSocket(mode === "live" && mission.ready);
  const replay = useReplay(mode === "replay");
  const mp4Export = useMp4Export(replay, mapExporterRef);
  // Keep the visual shell useful before a backend is started. The first real
  // mission frame automatically replaces this local-only demo frame.
  const sourceFrame = mode === "live"
    ? (live.frame && live.frame.episode_id === mission.state.episode_id && live.frame.frame_id >= (mission.frame?.frame_id ?? 0)
      ? live.frame : mission.frame) || DEMO_FRAME
    : replay.frame;
  const frame = useMemo(
    () => applyInformationField(
      sourceFrame,
      mode === "live" ? live.information : null,
    ),
    [live.information, mode, sourceFrame],
  );
  const readOnly = mode === "replay" || !mission.ready || frame?.episode_id === "local-demo";
  const editingAllowed = mode === "live" && showScenario && scene?.episode_id === frame?.episode_id && Boolean(scene?.vessel_mutation_allowed);
  const displayFrame = useMemo(() => frame && showScenario && scene?.episode_id === frame.episode_id
    ? { ...frame, scenario_vessels: scene.scenario_vessels || [], vessel_mutation_allowed: scene.vessel_mutation_allowed }
    : frame, [frame, scene, showScenario]);
  const vesselCommandBusy = vesselCommandStatus?.status === "queued";

  useEffect(() => {
    if (!showScenario || mode !== "live" || !mission.ready) { setScene(null); return undefined; }
    let disposed = false;
    const refresh = async () => {
      try {
        const response = await fetch("/api/scene");
        if (!response.ok) throw new Error(`scene_${response.status}`);
        const next = await response.json();
        if (!disposed) { setScene(next); setSceneError(""); }
      } catch (error) { if (!disposed) setSceneError(error.message); }
    };
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [showScenario, mode, mission.ready, frame?.episode_id]);

  useEffect(() => {
    setSelectionMode(false);
    setSelectedBBox(null);
    setSelectedContactId(null);
    setVesselPlacement(null);
    setSelectedScenarioVesselId(null);
    setVesselCommandStatus(null);
    setLiveEvents([]);
    setLastLlmCycle(null);
    setSelectedUavId(null);
    setSelectedMessageId(null);
    setSelectedPlanId(null);
    setMapMode("situation");
    setScene(null);
  }, [mode, frame?.episode_id]);

  useEffect(() => {
    if (!editingAllowed) {
      setVesselPlacement(null);
      setSelectedScenarioVesselId(null);
    }
  }, [editingAllowed]);

  useEffect(() => {
    if (mode !== "live" || !live.frame) return;
    setLiveEvents((current) => {
      const incoming = live.frame.events || [];
      const keys = new Set(current.map((event) => `${event.time}|${event.type}|${JSON.stringify(event.data)}`));
      const merged = [...current];
      for (const event of incoming) {
        const key = `${event.time}|${event.type}|${JSON.stringify(event.data)}`;
        if (!keys.has(key)) merged.push(event);
      }
      return merged.slice(-300);
    });
    if (live.frame.llm_cycle) setLastLlmCycle(live.frame.llm_cycle);
  }, [live.frame, mode]);

  const replayEvents = useMemo(() => {
    if (mode !== "replay") return [];
    return replay.markers.map((marker) => marker.event);
  }, [mode, replay.markers]);

  const replayLlmCycle = useMemo(() => {
    if (mode !== "replay") return null;
    return replay.frame?.llm_cycle || null;
  }, [mode, replay.frame]);
  const displayedLlmCycle = mode === "replay" ? replayLlmCycle : lastLlmCycle;

  const replayConnectionStatus = replay.targetLoadingIndex != null || replay.loading
    ? "connecting"
    : replay.error ? "error" : "connected";
  const replayConnectionLabel = replay.targetLoadingIndex != null
    ? "载入目标帧"
    : replay.error || (replay.loading ? "载入中" : `${replay.frames.length} 帧`);

  useEffect(() => {
    if (selectedUavId && frame && !(frame.uavs || []).some((uav) => uav.id === selectedUavId)) {
      setSelectedUavId(null);
    }
    if (selectedContactId && frame && !(frame.contacts || []).some((contact) => contact.contact_id === selectedContactId)) {
      setSelectedContactId(null);
    }
  }, [frame, selectedContactId, selectedUavId]);

  const commandId = () => globalThis.crypto?.randomUUID?.()
    || `vessel-${Date.now()}-${Math.random().toString(16).slice(2)}`;

  const pollVesselCommand = async (id) => {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, attempt ? 150 : 0));
      const response = await fetch(`/api/vessel-commands/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error(`command_${response.status}`);
      const result = await response.json();
      if (result.status !== "queued") return result;
    }
    throw new Error("command_timeout");
  };

  const submitVesselCommand = async ({ url, method, body }, successMessage, onApplied) => {
    const id = body.command_id;
    setVesselCommandStatus({ status: "queued", message: "船舶命令排队中", commandId: id });
    try {
      const response = await fetch(url, {
        method,
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error_code || "vessel_create_failed");
      const applied = await pollVesselCommand(result.command_id || id);
      setVesselCommandStatus({
        status: applied.status,
        message: applied.status === "applied" ? successMessage : "船舶命令被拒绝",
        commandId: applied.command_id || id,
        errorCode: applied.error_code,
      });
      if (applied.status === "applied") onApplied?.(applied);
    } catch (error) {
      setVesselCommandStatus({ status: "rejected", message: "船舶命令失败", errorCode: error.message });
    }
  };

  const handlePlaceVessel = async (position, selectedType = vesselPlacement) => {
    if (!editingAllowed || vesselCommandBusy || !selectedType || !frame?.episode_id) return;
    const id = commandId();
    await submitVesselCommand({
      url: "/api/vessels",
      method: "POST",
      body: {
        episode_id: frame.episode_id,
        command_id: id,
        vessel_class: selectedType,
        position_cells: position,
      },
    }, "船舶已加入场景", () => setVesselPlacement(null));
  };

  const handleDeleteVessel = async () => {
    if (!editingAllowed || vesselCommandBusy || !selectedScenarioVesselId || !frame?.episode_id) return;
    const vessel = (scene?.scenario_vessels || []).find(
      (item) => item.scenario_entity_id === selectedScenarioVesselId,
    );
    if (!vessel) return;
    const id = commandId();
    await submitVesselCommand({
      url: `/api/vessels/${encodeURIComponent(vessel.scenario_entity_id)}`,
      method: "DELETE",
      body: {
        episode_id: frame.episode_id,
        command_id: id,
        expected_revision: vessel.revision,
      },
    }, "船舶已删除", () => setSelectedScenarioVesselId(null));
  };

  const handleSetVesselAis = async (enabled) => {
    if (!editingAllowed || vesselCommandBusy || !selectedScenarioVesselId || !frame?.episode_id) return;
    const vessel = (scene?.scenario_vessels || []).find(
      (item) => item.scenario_entity_id === selectedScenarioVesselId,
    );
    if (!vessel?.ais_controllable || vessel.ais_enabled === enabled) return;
    const id = commandId();
    await submitVesselCommand({
      url: `/api/vessels/${encodeURIComponent(vessel.scenario_entity_id)}/ais`,
      method: "PATCH",
      body: {
        episode_id: frame.episode_id,
        command_id: id,
        expected_revision: vessel.revision,
        ais_enabled: enabled,
      },
    }, enabled ? "AIS 已开启" : "AIS 已关闭");
  };

  const connectionLabel = {
    idle: "待机",
    connecting: "连接中",
    connected: "实时连接",
    reconnecting: "正在重连",
    error: "数据错误",
  }[live.status] || live.status;
  const displayedConnectionLabel = sourceFrame?.episode_id !== "local-demo"
    ? (live.status === "connected" ? connectionLabel : mission.connection === "reconnecting" ? "数据已过期 · 正在重连" : "HTTP 轮询 · 正在重连") : "本地演示 · 等待后端";

  return (
    <main className={`app-layout mission-v2 ${mode === "replay" ? "replay-active" : ""} ${sidebarOpen ? "" : "sidebar-hidden"} ${pluginView ? "plugin-view" : ""}`}>
      <header className="top-bar">
        <div className="product-mark" aria-label="多 UUV 协同任务控制台">
          <span className="mark-index">MC</span>
          <span>多 UUV 协同任务控制台</span>
        </div>
        <div className="mode-switch" aria-label="数据模式">
          <button className={mode === "live" ? "active" : ""} onClick={() => setMode("live")}>
            <Radio size={15} />直播
          </button>
          <button className={mode === "replay" ? "active" : ""} onClick={() => setMode("replay")}>
            <History size={15} />回放
          </button>
        </div>
        {mode === "replay" && (
          <select
            className="file-select"
            value={replay.selectedFile}
            onChange={(event) => replay.load(event.target.value)}
            aria-label="选择回放文件"
          >
            <option value="">选择任务记录</option>
            {replay.files.map((file) => <option key={file} value={file}>{file}</option>)}
          </select>
        )}
        <span className={`connection-state ${mode === "live" ? live.status : replayConnectionStatus}`}>
          <span className="connection-dot" />
          {mode === "live" ? displayedConnectionLabel : replayConnectionLabel}
        </span>
        <div className="top-actions">
          <button
            type="button"
            className={`icon-btn rec-btn ${recording.status.status === "recording" ? "recording-active" : ""}`}
            aria-label={recordingView.action === "stop" ? "停止录制并导出" : "开始录制界面"}
            title={recordingView.action === "stop" ? "停止录制并导出 MP4" : "录制完整实时界面"}
            disabled={!recordingView.action || recording.busy || (recordingView.action === "start" && (mode !== "live" || !mission.ready))}
            onClick={recordingView.action === "stop" ? recording.stop : recording.start}
          >{recordingView.action === "stop" ? <Square size={14} fill="currentColor" /> : <Circle size={15} fill="currentColor" />}</button>
          <div className="map-mode-switch" role="group" aria-label="地图图层">
            <button type="button" className={mapMode === "situation" ? "active" : ""} aria-pressed={mapMode === "situation"} onClick={() => setMapMode("situation")}>态势</button>
            <button type="button" className={mapMode === "planning" ? "active" : ""} aria-pressed={mapMode === "planning"} onClick={() => setMapMode("planning")}>规划</button>
          </div>
          <div className="trail-mode-switch" role="group" aria-label="UUV轨迹显示模式">
            <button
              className={trailMode === "full" ? "active" : ""}
              onClick={() => setTrailMode("full")}
              title="完整 UUV 轨迹"
              aria-label="完整 UUV 轨迹"
              aria-pressed={trailMode === "full"}
            >
              <Route size={16} />
            </button>
            <button
              className={trailMode === "tail" ? "active" : ""}
              onClick={() => setTrailMode("tail")}
              title="渐变长尾 UUV 轨迹"
              aria-label="渐变长尾 UUV 轨迹"
              aria-pressed={trailMode === "tail"}
            >
              <Wind size={16} />
            </button>
            <button
              className={trailMode === "comet" ? "active" : ""}
              onClick={() => setTrailMode("comet")}
              title="彗星拖尾 UUV 轨迹"
              aria-label="彗星拖尾 UUV 轨迹"
              aria-pressed={trailMode === "comet"}
            >
              <span style={{ fontSize: 13, lineHeight: 1 }}>☄</span>
            </button>
          </div>
          <button
            className="trail-mode-compact"
            onClick={() => setTrailMode((value) => {
              if (value === "full") return "tail";
              if (value === "tail") return "comet";
              return "full";
            })}
            title={`UUV 轨迹: ${trailMode === "full" ? "完整" : trailMode === "tail" ? "渐变长尾" : "彗星拖尾"} — 点击切换`}
            aria-label="切换 UUV 轨迹显示模式"
          >
            {trailMode === "full" ? <Route size={17} /> : trailMode === "tail" ? <Wind size={17} /> : <span style={{ fontSize: 14 }}>☄</span>}
          </button>
          <button className={showGrid ? "icon-btn active" : "icon-btn"} onClick={() => setShowGrid((value) => !value)} title="网格" aria-label="切换网格">
            <Grid3X3 size={17} />
          </button>
          <button className={drawerVisible ? "icon-btn active" : "icon-btn"} onClick={() => setDrawerVisible((value) => !value)} title="任务详情" aria-label="切换任务详情面板" aria-pressed={drawerVisible}>
            <PanelBottom size={17} />
          </button>
          <button
            className={selectionMode ? "icon-btn active" : "icon-btn"}
            onClick={() => setSelectionMode((value) => !value)}
            title={readOnly ? "回放只读" : "框选重点区"}
            aria-label="框选重点区"
            aria-pressed={selectionMode}
            disabled={readOnly}
          >
            <Focus size={17} />
          </button>
          <button className={pluginView ? "icon-btn active" : "icon-btn"} onClick={() => setPluginView((value) => !value)} title="插件视图" aria-label="切换插件视图" aria-pressed={pluginView}>
            <Workflow size={17} />
          </button>
          <button className={sidebarOpen ? "icon-btn active" : "icon-btn"} onClick={() => setSidebarOpen((value) => !value)} title="任务工作区侧边栏" aria-label="切换任务工作区侧边栏" aria-pressed={sidebarOpen}>
            <PanelRight size={17} />
          </button>
        </div>
        <MissionControl mission={mission} frame={frame} readOnly={readOnly}
          recordingStatus={recordingView.label}
          recordingState={recordingView.alert ? "unavailable" : recording.status.status} />
      </header>

      <CanvasMap
        ref={mapExporterRef}
        frame={displayFrame}
        candidate={mode === "live" ? mission.candidate : null}
        mapMode={mapMode}
        selectedPlanId={selectedPlanId}
        selectedUavId={selectedUavId}
        onSelectUav={setSelectedUavId}
        showGrid={showGrid}
        showScenario={showScenario || Boolean(vesselPlacement)}
        trailMode={trailMode}
        selectionMode={selectionMode}
        onSelectionCommit={(bbox) => { setSelectedBBox(bbox); setSidebarOpen(true); }}
        onSelectContact={setSelectedContactId}
        selectedContactId={selectedContactId}
        placementMode={Boolean(vesselPlacement && editingAllowed && !vesselCommandBusy)}
        onPlaceVessel={handlePlaceVessel}
        onDropVessel={(vesselClass, position) => {
          if (editingAllowed && !vesselCommandBusy) {
            setVesselPlacement(vesselClass);
            handlePlaceVessel(position, vesselClass);
          }
        }}
        selectedScenarioVesselId={selectedScenarioVesselId}
        onSelectScenarioVessel={setSelectedScenarioVesselId}
      />
      <PluginPanel
        frame={displayFrame}
        events={mode === "live" ? mission.state.events || liveEvents : replayEvents}
      />
      <RightSidebar
        mission={mission}
        frame={displayFrame}
        selectedUavId={selectedUavId}
        onSelectUav={setSelectedUavId}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        lastLlmCycle={displayedLlmCycle}
        readOnly={readOnly}
        connectionStatus={mode === "live" ? live.status : replayConnectionStatus}
        selection={selectedBBox}
        onClearSelection={() => setSelectedBBox(null)}
        selectedContactId={selectedContactId}
        onSelectContact={setSelectedContactId}
        editingAllowed={editingAllowed}
        vesselPlacement={vesselPlacement}
        onSelectVesselType={setVesselPlacement}
        onCancelVesselPlacement={() => setVesselPlacement(null)}
        selectedScenarioVesselId={selectedScenarioVesselId}
        onSelectScenarioVessel={setSelectedScenarioVesselId}
        onDeleteVessel={handleDeleteVessel}
        onSetVesselAis={handleSetVesselAis}
        vesselCommandStatus={vesselCommandStatus}
        selectedMessageId={selectedMessageId}
        sceneVisible={showScenario}
        onToggleScene={setShowScenario}
      />
      {showScenario && <div className="scene-debug-indicator" role="status">场景调试真值{sceneError ? ` · ${sceneError}` : ""}</div>}
      <BottomDrawer
        frame={frame}
        events={mode === "live" ? mission.state.events || liveEvents : replayEvents}
        llmCycle={displayedLlmCycle}
        visible={drawerVisible}
        onToggle={() => setDrawerVisible((value) => !value)}
        mission={mission}
        readOnly={readOnly}
        selection={selectedBBox}
        onSelectDecision={(trace) => {
          setSelectedPlanId(trace.planId);
          setSelectedUavId(trace.members[0] || null);
          setSelectedContactId(trace.contactId);
          setMapMode("planning");
          if (mode === "live") mission.preview(trace.planId);
        }}
        onSelectEvent={(event) => {
          const data = event.data || {};
          const uuv = data.uav_id || data.uuv_id || data.observer_id || data.members?.[0];
          if (uuv) setSelectedUavId(uuv);
          if (data.contact_id) setSelectedContactId(data.contact_id);
          if (data.message_id) { setSelectedMessageId(data.message_id); setSidebarOpen(true); }
          if (data.plan_id) { setSelectedPlanId(data.plan_id); setMapMode("planning"); if (mode === "live") mission.preview(data.plan_id); }
        }}
      />
      <PlaybackBar
        visible={mode === "replay"}
        isPlaying={replay.isPlaying}
        onPlayPause={() => replay.setIsPlaying((value) => !value)}
        frameIndex={replay.index}
        totalFrames={replay.total || replay.frames.length}
        onSeek={replay.seek}
        playSpeed={replay.speed}
        onSpeedChange={replay.setSpeed}
        frame={frame}
        markers={replay.markers}
        loadedFrames={replay.loadedFrameCount}
        targetLoadingIndex={replay.targetLoadingIndex}
        onExportMp4={mp4Export.exportMp4}
        exportAvailable={mp4Export.available}
        exporting={mp4Export.exporting}
        exportProgress={mp4Export.progress}
        exportError={mp4Export.error}
      />
    </main>
  );
}
