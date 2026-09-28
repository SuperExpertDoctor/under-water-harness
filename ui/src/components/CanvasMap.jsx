import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from "react";
import { RadioTower, Radar, Crosshair, Route, LogOut } from "lucide-react";

import { computeLayout, dragToBBox, pixelToCoord } from "../renderer/geometry";
import { renderFrame } from "../renderer/layers";
import { drawMissionOverlay } from "../renderer/missionOverlay";
import { interpolateUuv } from "../state/missionState";
import { ownerColor } from "../renderer/colors";

export function readCellInformation(frame, cell) {
  if (!cell || !frame?.info_matrix?.[cell.col] || frame.info_matrix[cell.col][cell.row] == null) return null;
  const scan = Number(frame.info_matrix[cell.col][cell.row]);
  const target = Number(frame.target_info_matrix?.[cell.col]?.[cell.row] || 0);
  return { col: cell.col, row: cell.row,
    I: Number.isFinite(scan) ? Math.max(0, Math.min(1, scan)) : 0,
    V: Number.isFinite(target) ? Math.max(0, Math.min(1, target)) : 0 };
}

export function InformationLegend() {
  return <div className="information-legend" aria-label="信息强度图例">
    {[["scan", "扫描新鲜度", "216, 197, 131", .3], ["target", "目标线索", "245, 157, 121", .28]].map(([id, label, rgb, opacity]) =>
      <span className="information-key" key={id}><span>{label}</span><small>0</small>
        <span className="information-ramp" aria-hidden="true">{[0, .25, .5, .75, 1].map((value) =>
          <i key={value} style={{ backgroundColor: `rgba(${rgb}, ${value * opacity})` }} />)}</span><small>100%</small>
      </span>)}
  </div>;
}

export function MapSummary({ frame, selectedUavId, onSelectUav }) {
  const boats = frame?.uavs || [];
  const regions = frame?.search_regions || [];
  const transit = boats.filter((boat) => boat.operation_mode === "track" && ["transit", "tracking_transit", "acquire", "acquiring"].includes(boat.task_phase)).length;
  const tracking = boats.filter((boat) => boat.effective_tracking === true).length;
  const exiting = boats.filter((boat) => ["exit", "exiting"].includes(boat.task_phase)).length;
  const contact = frame?.contacts?.[0];
  const contactState = !contact ? "尚未发现" : contact.state === "lost" ? "目标失联 · 预测" : contact.state === "tracking" ? "目标跟踪中" : "目标估计";
  return <div className="map-summary">
    <div className="map-situation">
      <strong><Radar size={16} />任务海域 <span>{frame?.task_area?.width_km || 4} × {frame?.task_area?.height_km || 4} km</span></strong>
      <div className="map-counters"><span>搜索 <b>{regions.length}</b></span><span><Route size={13} />跟踪转场 <b>{transit}</b></span><span><Crosshair size={13} />有效跟踪 <b>{tracking}</b></span><span><LogOut size={13} />驶离 <b>{exiting}</b></span></div>
      <span className="map-coverage">覆盖 <b>{Number(frame?.coverage_pct || 0).toFixed(1)}%</b></span>
    </div>
    <div className="map-responsibilities" aria-label="搜索区域责任艇">
      {regions.map((region) => <button key={region.id} type="button" className={selectedUavId === region.assigned_uav_id ? "owner-key active" : "owner-key"}
        aria-pressed={selectedUavId === region.assigned_uav_id} title={`${region.id} · ${region.assigned_uav_id}`}
        onClick={() => onSelectUav?.(selectedUavId === region.assigned_uav_id ? null : region.assigned_uav_id)}>
        <i style={{ backgroundColor: ownerColor(region.assigned_uav_id) }} /><span>{region.assigned_uav_id}</span><small>{Math.round(region.completion_pct || 0)}%</small>
      </button>)}
      {!regions.length && <span className="no-search-regions">搜索区域待分配</span>}
      <span className={`contact-state ${contact?.state || "undetected"}`}>{contactState}</span>
    </div>
  </div>;
}

const MAP_ASSET_SOURCES = {
  background: "/assets/background.png",
  uav: "/assets/uuv.png?v=20260928",
  submarine: "/assets/submarine-transparent.png?v=20260928",
  carrier: "/assets/carrier.png?v=20260801",
  destroyer: "/assets/destroyer.png?v=20260801",
};

function loadMapAsset(source) {
  return new Promise((resolve) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = source;
  });
}

function frameGridResolution(frame) {
  const cols = frame?.info_matrix?.length
    || Math.round(Number(frame?.task_area?.width_km || 0) / Number(frame?.task_area?.cell_size_km || 0))
    || 30;
  const rows = frame?.info_matrix?.[0]?.length
    || Math.round(Number(frame?.task_area?.height_km || 0) / Number(frame?.task_area?.cell_size_km || 0))
    || 30;
  return { cols, rows };
}

const CanvasMap = forwardRef(function CanvasMap({
  frame,
  candidate,
  selectedUavId,
  onSelectUav,
  showGrid = false,
  showScenario = false,
  trailMode = "tail",
  selectionMode = false,
  onSelectionCommit,
  onSelectContact,
  selectedContactId,
  placementMode = false,
  onPlaceVessel,
  onDropVessel,
  selectedScenarioVesselId,
  onSelectScenarioVessel,
}, ref) {
  const canvasRef = useRef(null);
  const containerRef = useRef(null);
  const layoutRef = useRef({ cellSize: 20, offsetX: 0, offsetY: 0 });
  const hoverRef = useRef(null);
  const [hovered, setHovered] = useState(false);
  const [hoverVersion, setHoverVersion] = useState(0);
  const prevFrameRef = useRef(null);
  const targetFrameRef = useRef(null);
  const frameReceivedRef = useRef(0);
  const [sizeVersion, setSizeVersion] = useState(0);
  const [mapAssets, setMapAssets] = useState({});
  const exportingRef = useRef(false);
  const selectionRef = useRef(null);
  const [selection, setSelection] = useState(null);

  useEffect(() => {
    let disposed = false;
    Promise.all(Object.entries(MAP_ASSET_SOURCES).map(async ([key, source]) => [
      key,
      await loadMapAsset(source),
    ])).then((entries) => {
      if (!disposed) setMapAssets(Object.fromEntries(entries.filter(([, image]) => image)));
    });
    return () => { disposed = true; };
  }, []);

  const updateSize = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;

    const width = Math.max(1, container.clientWidth);
    const height = Math.max(1, container.clientHeight);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    const { cols, rows } = frameGridResolution(targetFrameRef.current);
    layoutRef.current = computeLayout(width, height, cols, rows, { includeLegend: false });
    setSizeVersion((version) => version + 1);
  }, []);

  useEffect(() => {
    updateSize();
    const observer = new ResizeObserver(updateSize);
    if (containerRef.current) observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [updateSize]);

  useEffect(() => {
    if (!frame) {
      prevFrameRef.current = null;
      targetFrameRef.current = null;
      hoverRef.current = null;
      const canvas = canvasRef.current;
      canvas?.getContext("2d").clearRect(0, 0, canvas.width, canvas.height);
      return;
    }
    prevFrameRef.current = targetFrameRef.current?.episode_id === frame.episode_id ? targetFrameRef.current : null;
    targetFrameRef.current = frame;
    frameReceivedRef.current = performance.now();
    updateSize();
  }, [frame, updateSize]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const context = canvas.getContext("2d");
    if (!targetFrameRef.current) return undefined;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let animationFrame = null;
    let phase = 0;
    const INTERP_MS = 180;

    const lerpAngle = (a, b, k) => {
      if (a == null || b == null) return b ?? a ?? 0;
      let d = ((b - a) % 360 + 540) % 360 - 180;
      return a + d * k;
    };

    const render = () => {
      if (exportingRef.current) return;
      const prev = prevFrameRef.current;
      const target = targetFrameRef.current;
      let displayFrame = target;

      if (prev && target) {
        const elapsed = performance.now() - frameReceivedRef.current;
        const raw = Math.min(1, elapsed / INTERP_MS);
        if (raw < 1) {
          const t = 1 - (1 - raw) ** 2; // ease-out quad

          const prevUavMap = new Map();
          for (const u of prev.uavs || []) prevUavMap.set(u.id, u);
          const prevShipMap = new Map();
          for (const s of prev.ships || []) prevShipMap.set(s.id, s);

          const uavs = (target.uavs || []).map((u) => interpolateUuv(prevUavMap.get(u.id), u, t));

          const ships = (target.ships || []).map((s) => {
            const prevS = prevShipMap.get(s.id);
            if (!prevS) return s;
            return {
              ...s,
              position: [
                prevS.position[0] + (s.position[0] - prevS.position[0]) * t,
                prevS.position[1] + (s.position[1] - prevS.position[1]) * t,
              ],
              heading_deg: lerpAngle(prevS.heading_deg, s.heading_deg, t),
            };
          });

          displayFrame = { ...target, uavs, ships };
        }
      }

      const {
        cellSize, offsetX, offsetY, mapBounds, legendBounds, gridCols, gridRows,
      } = layoutRef.current;
      context.save();
      renderFrame(context, displayFrame, {
        cellSize,
        offsetX,
        offsetY,
        mapBounds,
        legendBounds,
        gridCols,
        gridRows,
        showGrid,
        showScenario,
        trailMode,
        selectedContactId,
        selectedScenarioVesselId,
        hoverInfo: readCellInformation(displayFrame, hoverRef.current),
        selectedUavId,
        frameCount: phase,
        assets: mapAssets,
      });
      drawMissionOverlay(context, displayFrame, candidate, layoutRef.current);
      context.restore();
      phase += 1;
      const elapsed = performance.now() - frameReceivedRef.current;
      if (!reducedMotion && prev && target && elapsed < INTERP_MS) {
        animationFrame = window.requestAnimationFrame(render);
      }
    };

    render();
    return () => {
      if (animationFrame) window.cancelAnimationFrame(animationFrame);
    };
  }, [candidate, frame, hoverVersion, mapAssets, selectedContactId, selectedScenarioVesselId, selectedUavId, showGrid, showScenario, sizeVersion, trailMode]);

  useImperativeHandle(ref, () => ({
    async recordReplay(frames, { fps = 20, onProgress } = {}) {
      const canvas = canvasRef.current;
      if (!canvas?.captureStream || !window.MediaRecorder) {
        throw new Error("This browser cannot record the mission map");
      }
      if (!frames?.length) throw new Error("No replay frames available");
      const mimeType = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"]
        .find((candidate) => MediaRecorder.isTypeSupported(candidate));
      if (!mimeType) throw new Error("This browser does not support WebM recording");

      const context = canvas.getContext("2d");
      const stream = canvas.captureStream(fps);
      const chunks = [];
      const recorder = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 7_000_000 });
      const finished = new Promise((resolve, reject) => {
        recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
        recorder.onstop = () => resolve(new Blob(chunks, { type: mimeType }));
        recorder.onerror = () => reject(new Error("Mission map recording failed"));
      });

      exportingRef.current = true;
      recorder.start();
      try {
        for (let index = 0; index < frames.length; index += 1) {
          const {
            cellSize, offsetX, offsetY, mapBounds, legendBounds, gridCols, gridRows,
          } = layoutRef.current;
          context.save();
          renderFrame(context, frames[index], {
            cellSize, offsetX, offsetY, mapBounds, legendBounds, gridCols, gridRows,
            showGrid, showScenario, trailMode, hoverInfo: null, selectedUavId,
            selectedContactId,
            selectedScenarioVesselId,
            frameCount: index, assets: mapAssets,
          });
          drawMissionOverlay(context, frames[index], null, layoutRef.current);
          context.restore();
          onProgress?.((index + 1) / frames.length);
          await new Promise((resolve) => window.setTimeout(resolve, 1000 / fps));
        }
      } finally {
        exportingRef.current = false;
        recorder.stop();
        stream.getTracks().forEach((track) => track.stop());
      }
      return finished;
    },
  }), [mapAssets, selectedContactId, selectedScenarioVesselId, selectedUavId, showGrid, showScenario, trailMode]);

  const handleMouseMove = useCallback((event) => {
    const canvas = canvasRef.current;
    if (!canvas || !frame) return;
    const rect = canvas.getBoundingClientRect();
    const { cellSize, offsetX, offsetY, gridCols, gridRows } = layoutRef.current;
    const coord = pixelToCoord(
      event.clientX - rect.left,
      event.clientY - rect.top,
      cellSize,
      offsetX,
      offsetY,
      gridCols,
      gridRows,
    );

    if (coord && frame.info_matrix) {
      hoverRef.current = coord;
      setHovered(true);
      setHoverVersion((version) => version + 1);
    } else {
      hoverRef.current = null;
      setHovered(false);
      setHoverVersion((version) => version + 1);
    }
  }, [frame]);

  const pointerPosition = useCallback((event) => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }, []);

  const isInsideTask = useCallback((point) => {
    const bounds = layoutRef.current.taskBounds;
    return Boolean(bounds && point
      && point.x >= bounds.x && point.x <= bounds.x + bounds.width
      && point.y >= bounds.y && point.y <= bounds.y + bounds.height);
  }, []);

  const cancelSelection = useCallback(() => {
    const current = selectionRef.current;
    if (current?.pointerId != null && canvasRef.current?.hasPointerCapture(current.pointerId)) {
      canvasRef.current.releasePointerCapture(current.pointerId);
    }
    selectionRef.current = null;
    setSelection(null);
  }, []);

  useEffect(() => {
    if (!selectionMode) cancelSelection();
  }, [cancelSelection, selectionMode]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") cancelSelection();
    };
    const onBlur = () => cancelSelection();
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("blur", onBlur);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("blur", onBlur);
    };
  }, [cancelSelection]);

  const handlePointerDown = useCallback((event) => {
    if (!selectionMode || event.button !== 0) return;
    const point = pointerPosition(event);
    if (!isInsideTask(point)) return;
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    selectionRef.current = { pointerId: event.pointerId, start: point, end: point };
    setSelection({ start: point, end: point });
  }, [isInsideTask, pointerPosition, selectionMode]);

  const handlePointerMove = useCallback((event) => {
    handleMouseMove(event);
    const current = selectionRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const point = pointerPosition(event);
    current.end = point;
    setSelection({ start: current.start, end: point });
  }, [handleMouseMove, pointerPosition]);

  const handlePointerUp = useCallback((event) => {
    const current = selectionRef.current;
    if (!current || current.pointerId !== event.pointerId) return;
    const point = pointerPosition(event);
    current.end = point;
    const bbox = dragToBBox(current.start, point, layoutRef.current, layoutRef.current.gridCols, layoutRef.current.gridRows);
    cancelSelection();
    if (bbox) onSelectionCommit?.(bbox);
  }, [cancelSelection, onSelectionCommit, pointerPosition]);

  const handleMouseLeave = useCallback(() => {
    hoverRef.current = null;
    setHovered(false);
    setHoverVersion((version) => version + 1);
  }, []);

  const handleClick = useCallback((event) => {
    if (selectionMode) return;
    handleMouseMove(event);
    const canvas = canvasRef.current;
    if (!canvas || !frame) return;
    const point = pointerPosition(event);
    if (!point) return;
    const { x: mouseX, y: mouseY } = point;
    const { cellSize, offsetX, offsetY, gridCols, gridRows } = layoutRef.current;

    if (placementMode) {
      const coord = pixelToCoord(mouseX, mouseY, cellSize, offsetX, offsetY, gridCols, gridRows);
      if (coord) onPlaceVessel?.([coord.col + 0.5, coord.row + 0.5]);
      return;
    }

    for (const uav of frame.uavs) {
      const [col, row] = uav.position;
      const centerX = offsetX + (col + 0.5) * cellSize;
      const centerY = offsetY + (row + 0.5) * cellSize;
      if (Math.hypot(mouseX - centerX, mouseY - centerY) < Math.max(9, cellSize * 0.55)) {
        onSelectUav?.(uav.id === selectedUavId ? null : uav.id);
        return;
      }
    }
    for (const contact of frame.contacts || []) {
      const position = contact.estimated_position;
      if (!Array.isArray(position)) continue;
      const centerX = offsetX + (Number(position[0]) + 0.5) * cellSize;
      const centerY = offsetY + (Number(position[1]) + 0.5) * cellSize;
      if (Math.hypot(mouseX - centerX, mouseY - centerY) < Math.max(10, cellSize * 0.65)) {
        onSelectContact?.(contact.contact_id);
        return;
      }
    }
    for (const vessel of frame.scenario_vessels || []) {
      const position = vessel.position;
      if (!Array.isArray(position)) continue;
      const centerX = offsetX + (Number(position[0]) + 0.5) * cellSize;
      const centerY = offsetY + (Number(position[1]) + 0.5) * cellSize;
      if (Math.hypot(mouseX - centerX, mouseY - centerY) < Math.max(10, cellSize * 0.7)) {
        onSelectScenarioVessel?.(
          vessel.scenario_entity_id === selectedScenarioVesselId ? null : vessel.scenario_entity_id,
        );
        return;
      }
    }
  }, [frame, handleMouseMove, onPlaceVessel, onSelectContact, onSelectScenarioVessel, onSelectUav, placementMode, pointerPosition, selectedScenarioVesselId, selectedUavId, selectionMode]);

  const handleCellKey = (event) => {
    const directions = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    if (event.key === "Escape") { handleMouseLeave(); return; }
    const step = directions[event.key];
    if (!step) return;
    event.preventDefault();
    const { cols, rows } = frameGridResolution(frame);
    const current = hoverRef.current || { col: 0, row: 0 };
    hoverRef.current = { col: Math.max(0, Math.min(cols - 1, current.col + step[0])), row: Math.max(0, Math.min(rows - 1, current.row + step[1])) };
    setHoverVersion((version) => version + 1);
  };

  const handleDragOver = useCallback((event) => {
    if (!placementMode && !event.dataTransfer.types.includes("application/x-vessel-class")) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, [placementMode]);

  const handleDrop = useCallback((event) => {
    event.preventDefault();
    const vesselClass = event.dataTransfer.getData("application/x-vessel-class");
    if (!vesselClass) return;
    const point = pointerPosition(event);
    const { cellSize, offsetX, offsetY, gridCols, gridRows } = layoutRef.current;
    const coord = point && pixelToCoord(point.x, point.y, cellSize, offsetX, offsetY, gridCols, gridRows);
    if (!coord) return;
    onDropVessel?.(vesselClass, [coord.col + 0.5, coord.row + 0.5]);
  }, [onDropVessel, pointerPosition]);

  const selectionBox = selection?.start && selection?.end
    ? {
      left: Math.min(selection.start.x, selection.end.x),
      top: Math.min(selection.start.y, selection.end.y),
      width: Math.abs(selection.start.x - selection.end.x),
      height: Math.abs(selection.start.y - selection.end.y),
    }
    : null;

  const cellInfo = readCellInformation(frame, hoverRef.current);

  return (
    <div className="canvas-area">
      <MapSummary frame={frame} selectedUavId={selectedUavId} onSelectUav={onSelectUav} />
      <div className="map-stage" ref={containerRef}>
      <canvas
        ref={canvasRef}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={cancelSelection}
        onMouseLeave={handleMouseLeave}
        onClick={handleClick}
        tabIndex={0}
        onKeyDown={handleCellKey}
        onFocus={() => { if (!hoverRef.current) hoverRef.current = { col: 0, row: 0 }; setHoverVersion((version) => version + 1); }}
        onBlur={handleMouseLeave}
        aria-describedby="map-cell-information"
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        style={{ cursor: placementMode || selectionMode ? "crosshair" : hovered ? "crosshair" : "default", touchAction: "none" }}
        aria-label="Operational map"
      />
      {selectionBox && (
        <div
          className="selection-rect"
          style={selectionBox}
          aria-label="当前重点区框选"
        />
      )}
      {!frame && (
        <div className="map-empty" role="status">
          <RadioTower size={22} />
          <strong>WAITING FOR MISSION DATA</strong>
          <span>Live telemetry or replay frames will appear here.</span>
        </div>
      )}
      {candidate && candidate.episode_id === frame?.episode_id && <div className="map-candidate-label">候选预览 · {candidate.kind || candidate.algorithm} · {candidate.members?.join(", ")}</div>}
      <div className="map-scale" aria-hidden="true"><i style={{ width: layoutRef.current.cellSize * 5 }} />{Number(frame?.task_area?.cell_size_km || 0) * 5} KM</div>
      </div>
      <div className="map-information-bar">
        <InformationLegend />
        <output id="map-cell-information" aria-label="栅格信息" className="cell-information">
          <span>栅格 <b>{cellInfo ? `${cellInfo.col} / ${cellInfo.row}` : "--"}</b></span>
          <span>扫描 <b>{cellInfo ? `${(cellInfo.I * 100).toFixed(1)}%` : "--"}</b></span>
          <span>线索 <b>{cellInfo ? `${(cellInfo.V * 100).toFixed(1)}%` : "--"}</b></span>
        </output>
      </div>
    </div>
  );
});

export default CanvasMap;
