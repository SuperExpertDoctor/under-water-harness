import { useCallback, useEffect, useRef, useState } from "react";
import {
  createLayout,
  findUuvAtPoint,
  interpolateFrame,
  renderTelemetry,
} from "../renderer/telemetryRenderer";

const ASSETS = {
  background: "/assets/background.png",
  uuv: "/assets/uuv.png",
};

function loadImage(source) {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => resolve(null);
    image.src = source;
  });
}

export default function TelemetryMap({ frame, selectedUuvId, onSelectUuv }) {
  const canvasRef = useRef(null);
  const hostRef = useRef(null);
  const layoutRef = useRef(null);
  const previousRef = useRef(null);
  const currentRef = useRef(null);
  const receivedAtRef = useRef(0);
  const assetsRef = useRef({});
  const [renderVersion, setRenderVersion] = useState(0);

  const updateSize = useCallback(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host) return;
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
    layoutRef.current = createLayout(width, height, currentRef.current);
    setRenderVersion((value) => value + 1);
  }, []);

  useEffect(() => {
    let disposed = false;
    Promise.all(Object.entries(ASSETS).map(async ([key, path]) => [key, await loadImage(path)]))
      .then((entries) => {
        if (disposed) return;
        assetsRef.current = Object.fromEntries(entries.filter(([, image]) => image));
        setRenderVersion((value) => value + 1);
      });
    return () => { disposed = true; };
  }, []);

  useEffect(() => {
    if (!frame) return;
    previousRef.current = currentRef.current;
    currentRef.current = frame;
    receivedAtRef.current = performance.now();
    updateSize();
  }, [frame, updateSize]);

  useEffect(() => {
    updateSize();
    const observer = new ResizeObserver(updateSize);
    if (hostRef.current) observer.observe(hostRef.current);
    return () => observer.disconnect();
  }, [updateSize]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const context = canvas.getContext("2d");
    let animationFrame;
    const render = () => {
      const current = currentRef.current;
      const previous = previousRef.current;
      const progress = previous && current
        ? Math.min(1, (performance.now() - receivedAtRef.current) / 180)
        : 1;
      const displayFrame = interpolateFrame(previous, current, progress);
      context.clearRect(0, 0, canvas.clientWidth, canvas.clientHeight);
      renderTelemetry(context, displayFrame, layoutRef.current, assetsRef.current, selectedUuvId);
      if (previous && current && progress < 1) animationFrame = requestAnimationFrame(render);
    };
    render();
    return () => cancelAnimationFrame(animationFrame);
  }, [renderVersion, selectedUuvId]);

  const handleClick = useCallback((event) => {
    const canvas = canvasRef.current;
    const layout = layoutRef.current;
    const current = currentRef.current;
    if (!canvas || !layout || !current) return;
    const rect = canvas.getBoundingClientRect();
    const hit = findUuvAtPoint(current, layout, event.clientX - rect.left, event.clientY - rect.top);
    if (hit && hit.distance <= Math.max(16, layout.cellSize * 0.8)) {
      onSelectUuv?.(hit.uav.id === selectedUuvId ? null : hit.uav.id);
    }
  }, [onSelectUuv, selectedUuvId]);

  return (
    <section className="map-host" ref={hostRef}>
      <canvas ref={canvasRef} onClick={handleClick} aria-label="UUV 实时地图" />
      {!frame && <div className="map-waiting">等待后端实时数据...</div>}
      <div className="map-scale">每格 5 km</div>
    </section>
  );
}
