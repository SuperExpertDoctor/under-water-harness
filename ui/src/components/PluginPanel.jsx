import { useEffect, useMemo, useRef, useState } from "react";
import { Plus, Power, RotateCcw, Trash2, X, ZoomIn, ZoomOut } from "lucide-react";

import { PLUGIN_DEFS, PLUGIN_EDGES, derivePluginGraph, portText } from "../state/pluginGraph";

const STORAGE_KEY = "uuv.pluginLibrary.v1";
const NODE_W = 176;
const NODE_H = 96;

function loadLibrary() {
  try {
    const raw = globalThis.localStorage?.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return { disabled: parsed.disabled || [], custom: parsed.custom || [] };
    }
  } catch { /* fall through to defaults */ }
  return { disabled: [], custom: [] };
}

function portCount(kind) {
  return kind === "none" ? 0 : kind === "one" ? 1 : 3;
}

// Generates a drop-in plugin file conforming to plugins/contract.py; the
// backend validates and installs it into tools/uuv_game/plugins/.
function pluginSource(plugin) {
  return `"""Custom plugin installed from the plugin panel."""

PLUGIN = {
    "id": ${JSON.stringify(plugin.id)},
    "name": ${JSON.stringify(plugin.name)},
    "layer": ${plugin.layer},
    "color": ${JSON.stringify(plugin.color)},
    "desc": ${JSON.stringify(plugin.desc)},
    "inputs": ${JSON.stringify(plugin.inputs)},
    "outputs": ${JSON.stringify(plugin.outputs)},
    "core": False,
    "edges": [],
    "owns_stages": [],
}


def activity(runtime, ctx):
    ctx.meta("自定义插件已注册")
`;
}

export default function PluginPanel({ frame, events }) {
  const [library, setLibrary] = useState(loadLibrary);
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ name: "", desc: "" });
  const [pinned, setPinned] = useState(null);
  const [catalog, setCatalog] = useState(null);
  const [drags, setDrags] = useState({});
  const [dragging, setDragging] = useState(null);
  const [zoom, setZoom] = useState(1);
  const dragRef = useRef(null);
  const areaRef = useRef(null);
  const innerRef = useRef(null);
  const [size, setSize] = useState({ w: 900, h: 560 });

  // Plugin specs come from the backend registry so the panel renders whatever
  // the control stack declares; bundled defs remain the offline fallback.
  const fetchCatalog = () => {
    fetch("/api/plugins")
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => { if (data?.plugins?.length) setCatalog(data); })
      .catch(() => {});
  };
  useEffect(fetchCatalog, []);

  // One-time migration: plugins previously stored as display-only
  // localStorage entries are registered on the backend, then cleared.
  useEffect(() => {
    if (!library.custom.length) return;
    Promise.all(library.custom.map((plugin) => fetch("/api/plugins", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: pluginSource(plugin) }),
    }).catch(() => null))).then(() => {
      setLibrary((lib) => ({ ...lib, custom: [] }));
      fetchCatalog();
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const defs = catalog?.plugins || PLUGIN_DEFS;
  const edgeDefs = catalog?.edges || PLUGIN_EDGES;
  const edgeKey = (edge) => edge.key || `${edge.from}>${edge.to}`;
  const graph = useMemo(() => derivePluginGraph(frame, events), [frame, events]);
  const uiDisabled = (def) => !def.core && library.disabled.includes(def.id);
  const visibleDefs = defs.filter((d) => d.enabled !== false && !uiDisabled(d));

  useEffect(() => {
    try { globalThis.localStorage?.setItem(STORAGE_KEY, JSON.stringify(library)); } catch { /* storage optional */ }
  }, [library]);

  useEffect(() => {
    if (!areaRef.current) return undefined;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) setSize({ w: rect.width, h: rect.height });
    });
    observer.observe(areaRef.current);
    return () => observer.disconnect();
  }, []);

  // Layout: a rightward-growing tree. Each topology layer is a column at
  // fixed spacing; children cluster near their parents instead of spreading
  // evenly, and the canvas extends (and scrolls) as the tree fans out.
  const layout = useMemo(() => {
    const byLayer = new Map();
    for (const def of visibleDefs) {
      const list = byLayer.get(def.layer) || [];
      list.push(def);
      byLayer.set(def.layer, list);
    }
    const layers = [...byLayer.keys()].sort((a, b) => a - b);
    const xGap = NODE_W + 84;
    const rowGap = NODE_H + 22;
    const padX = NODE_W / 2 + 24;
    const padY = NODE_H / 2 + 16;
    const map = new Map();
    let maxRows = 1;
    layers.forEach((layer, layerIndex) => {
      const hints = byLayer.get(layer).map((def) => {
        const parents = edgeDefs
          .filter((edge) => edge.to === def.id && map.has(edge.from))
          .map((edge) => map.get(edge.from).y);
        return { def, hint: parents.length ? parents.reduce((s, y) => s + y, 0) / parents.length : null };
      }).sort((a, b) => (a.hint ?? Infinity) - (b.hint ?? Infinity));
      const used = new Set();
      let freeRow = 0;
      for (const item of hints) {
        let row = item.hint == null ? freeRow : Math.max(0, Math.round((item.hint - padY) / rowGap));
        while (used.has(row)) row += 1;
        used.add(row);
        freeRow = Math.max(freeRow, row + 1);
        map.set(item.def.id, { x: padX + layerIndex * xGap, y: padY + row * rowGap });
      }
      if (used.size) maxRows = Math.max(maxRows, Math.max(...used) + 1);
    });
    for (const [id, pos] of Object.entries(drags)) {
      if (map.has(id)) map.set(id, pos);
    }
    const canvasW = Math.max(padX * 2 + Math.max(0, layers.length - 1) * xGap + NODE_W, size.w);
    const canvasH = Math.max(padY * 2 + maxRows * rowGap, size.h - 34);
    return { positions: map, canvasW, canvasH };
  }, [visibleDefs, edgeDefs, size, drags]);
  const positions = layout.positions;

  const edgeGeometry = (edge) => {
    const a = positions.get(edge.from);
    const b = positions.get(edge.to);
    if (!a || !b) return null;
    const x1 = a.x + NODE_W / 2;
    const y1 = a.y;
    const x2 = b.x - NODE_W / 2;
    const y2 = b.y;
    const dx = Math.max(48, (x2 - x1) / 2);
    return {
      path: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`,
      mid: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 12 },
      head: { x: x2, y: y2 },
    };
  };

  // Node dragging: positions live in canvas space, pointer events in client
  // space — the inner canvas rect converts between them. A sub-3px press is a
  // click (pin toggle); a real drag persists until double-click resets it.
  const onNodePointerDown = (event, def) => {
    if (event.button !== 0 || !innerRef.current) return;
    const pos = positions.get(def.id);
    if (!pos) return;
    const rect = innerRef.current.getBoundingClientRect();
    dragRef.current = { id: def.id, ox: (event.clientX - rect.left) / zoom - pos.x, oy: (event.clientY - rect.top) / zoom - pos.y, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onNodePointerMove = (event, def) => {
    const drag = dragRef.current;
    if (!drag || drag.id !== def.id) return;
    const rect = innerRef.current.getBoundingClientRect();
    const nx = Math.min(Math.max((event.clientX - rect.left) / zoom - drag.ox, NODE_W / 2), layout.canvasW - NODE_W / 2);
    const ny = Math.min(Math.max((event.clientY - rect.top) / zoom - drag.oy, NODE_H / 2), layout.canvasH - NODE_H / 2);
    if (!drag.moved && Math.hypot(nx - positions.get(def.id).x, ny - positions.get(def.id).y) < 3) return;
    drag.moved = true;
    setDragging(def.id);
    setDrags((prev) => ({ ...prev, [def.id]: { x: nx, y: ny } }));
  };
  const onNodePointerUp = (event, def) => {
    const drag = dragRef.current;
    dragRef.current = null;
    setDragging(null);
    if (drag?.moved) return;
    setPinned((p) => (p === def.id ? null : def.id));
  };
  const resetDrag = (id) => setDrags((prev) => {
    const next = { ...prev };
    delete next[id];
    return next;
  });

  const togglePlugin = (id, enabled) => {
    fetch(`/api/plugins/${id}/enabled`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: !enabled }),
    }).then(() => fetchCatalog()).catch(() => {});
    if (pinned === id) setPinned(null);
  };
  const removePlugin = (id) => {
    fetch(`/api/plugins/${id}`, { method: "DELETE" })
      .then(() => fetchCatalog()).catch(() => {});
    if (pinned === id) setPinned(null);
  };
  const addPlugin = () => {
    const name = draft.name.trim();
    if (!name) return;
    const id = `custom-${Date.now().toString(36)}`;
    const source = pluginSource({
      id, name, desc: draft.desc.trim() || "自定义插件", layer: 4, color: "#6d28d9",
      inputs: "many", outputs: "many",
    });
    fetch("/api/plugins", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source }),
    }).then((response) => { if (response.ok) fetchCatalog(); }).catch(() => {});
    setDraft({ name: "", desc: "" });
    setAdding(false);
  };

  const edgeFor = (id) => pinned === id;

  return (
    <div className="plugin-panel" role="region" aria-label="插件视图">
      <aside className="plugin-library">
        <header className="plugin-library-head">
          <span>插件库</span>
          <span className="plugin-count">{defs.length}</span>
          <button type="button" className="icon-btn" title="添加插件" aria-label="添加插件" onClick={() => setAdding((v) => !v)}>
            {adding ? <X size={15} /> : <Plus size={15} />}
          </button>
        </header>
        {adding && (
          <div className="plugin-add">
            <input
              value={draft.name}
              onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              placeholder="插件名称"
              aria-label="插件名称"
            />
            <input
              value={draft.desc}
              onChange={(e) => setDraft((d) => ({ ...d, desc: e.target.value }))}
              placeholder="功能描述（可选）"
              aria-label="功能描述"
            />
            <button type="button" onClick={addPlugin} disabled={!draft.name.trim()}>添加</button>
          </div>
        )}
        <ul className="plugin-list">
          {defs.map((def) => {
            const enabled = def.enabled !== false && !uiDisabled(def);
            const custom = !def.core;
            const node = graph.nodes[def.id];
            const tip = (def.guidelines || []).join("\n");
            return (
              <li key={def.id} className={`plugin-card ${enabled ? "" : "off"}`} title={tip || undefined}>
                <div className="plugin-card-top">
                  <span className="plugin-dot" style={{ background: def.color }} />
                  <strong>{def.name}</strong>
                  {custom ? (
                    <button
                      type="button"
                      className={`plugin-power ${enabled ? "on" : ""}`}
                      title={enabled ? "关闭插件（停止参与调度与显示）" : "启用插件"}
                      aria-pressed={enabled}
                      onClick={() => togglePlugin(def.id, enabled)}
                    >
                      <Power size={13} />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="plugin-power locked"
                      title="基础控制算法插件，不可关闭"
                      aria-label={`${def.name} 不可关闭`}
                      disabled
                    >
                      <Power size={13} />
                    </button>
                  )}
                  {custom && (
                    <button type="button" className="plugin-remove" title="删除插件" aria-label={`删除 ${def.name}`} onClick={() => removePlugin(def.id)}>
                      <Trash2 size={13} />
                    </button>
                  )}
                </div>
                <p>{def.snippet || def.desc}</p>
                <div className="plugin-card-foot">
                  <span>{portText(def)}</span>
                  {enabled && node?.active && <em>运行中</em>}
                </div>
              </li>
            );
          })}
        </ul>
      </aside>

      <section className="plugin-graph" ref={areaRef} aria-label="插件连接关系">
        <div className="plugin-graph-scroll">
        <div style={{ width: layout.canvasW * zoom, height: layout.canvasH * zoom }}>
        <div className="plugin-graph-inner" ref={innerRef} style={{ width: layout.canvasW, height: layout.canvasH, transform: `scale(${zoom})`, transformOrigin: "0 0" }}>
        <svg className="plugin-edges" width={layout.canvasW} height={layout.canvasH} aria-hidden="true">
          <defs>
            <marker id="plugin-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
            </marker>
          </defs>
          {edgeDefs.map((edge) => {
            if (!positions.get(edge.from) || !positions.get(edge.to)) return null;
            const geo = edgeGeometry(edge);
            if (!geo) return null;
            const key = edgeKey(edge);
            const live = graph.edges.find((e) => e.key === key);
            const active = live?.active;
            const highlight = pinned && (edge.from === pinned || edge.to === pinned);
            const color = active ? "#0f766e" : highlight ? "#1d4ed8" : "#94a3b8";
            return (
              <g key={key} style={{ color }} className={`${active ? "edge-live" : ""} ${highlight ? "edge-hot" : ""}`}>
                <path d={geo.path} fill="none" stroke="currentColor" strokeWidth={active ? 2.4 : 1.4}
                  strokeDasharray={active ? "7 5" : "4 5"} markerEnd="url(#plugin-arrow)" opacity={active ? 0.95 : 0.45} />
                {active && live.subjects.length > 0 && (
                  <text x={geo.mid.x} y={geo.mid.y} textAnchor="middle" className="edge-label">
                    {live.subjects.slice(0, 3).join(" · ")}{live.subjects.length > 3 ? ` +${live.subjects.length - 3}` : ""}
                  </text>
                )}
              </g>
            );
          })}
        </svg>

        {visibleDefs.map((def) => {
          const pos = positions.get(def.id);
          if (!pos) return null;
          const node = graph.nodes[def.id] || { active: false, subjects: [], meta: null };
          const pins = portCount(def.inputs);
          const pouts = portCount(def.outputs);
          return (
            <div
              key={def.id}
              className={`plugin-node ${node.active ? "live" : ""} ${pinned === def.id ? "pinned" : ""} ${dragging === def.id ? "dragging" : ""}`}
              style={{ left: pos.x - NODE_W / 2, top: pos.y - NODE_H / 2, width: NODE_W, minHeight: NODE_H, borderColor: node.active ? def.color : undefined }}
              onPointerDown={(e) => onNodePointerDown(e, def)}
              onPointerMove={(e) => onNodePointerMove(e, def)}
              onPointerUp={(e) => onNodePointerUp(e, def)}
              onDoubleClick={() => resetDrag(def.id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setPinned((p) => (p === def.id ? null : def.id)); }}
              title={`${def.desc} — 拖拽移动 · 双击复位`}
            >
              <span className="plugin-ports in" aria-hidden="true">
                {Array.from({ length: pins }, (_, i) => <i key={i} style={{ background: def.color }} />)}
              </span>
              <span className="plugin-ports out" aria-hidden="true">
                {Array.from({ length: pouts }, (_, i) => <i key={i} style={{ background: def.color }} />)}
              </span>
              <header><span className="plugin-dot" style={{ background: def.color }} />{def.name}</header>
              <div className="plugin-node-meta">{node.active ? (node.meta || "运行中") : "待机"}</div>
              {node.subjects.length > 0 && (
                <div className="plugin-subjects">
                  {node.subjects.slice(0, 4).map((s) => <b key={s}>{s}</b>)}
                  {node.subjects.length > 4 && <b>+{node.subjects.length - 4}</b>}
                </div>
              )}
            </div>
          );
        })}
        </div>
        </div>
        </div>
        <div className="graph-zoom zoom-controls" role="group" aria-label="插件图缩放">
          <button type="button" onClick={() => setZoom((z) => Math.min(2.5, z * 1.25))} aria-label="放大" title="放大"><ZoomIn size={15} /></button>
          <button type="button" onClick={() => setZoom((z) => Math.max(0.4, z / 1.25))} aria-label="缩小" title="缩小"><ZoomOut size={15} /></button>
          <button type="button" onClick={() => setZoom(1)} aria-label="重置缩放" title="重置缩放"><RotateCcw size={15} /></button>
        </div>

        <footer className="plugin-sim">
          {graph.sim.simMin != null && <span>sim t = {graph.sim.simMin.toFixed(1)} min</span>}
          {graph.sim.frameId != null && <span>frame #{graph.sim.frameId}</span>}
          {graph.sim.cycle != null && <span>cycle {graph.sim.cycle}</span>}
          {graph.sim.mode && <span>权限 {graph.sim.mode}</span>}
          <span className="plugin-hint">{graph.source === "runtime" ? "实时调用 · 由运行时生成" : "逻辑视图 · 由实时状态推导"} · 拖拽移动节点 · 双击复位</span>
        </footer>
      </section>
    </div>
  );
}
