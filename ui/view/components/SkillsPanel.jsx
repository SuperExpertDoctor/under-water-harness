import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { RefreshCw } from "lucide-react";

import skillApi from "../../feed/api/skillApi";

const POLL_MS = 4000;

function fmt(v, suffix = "") {
  return v === null || v === undefined ? "—" : `${v}${suffix}`;
}

function MetricLine({ label, metrics }) {
  if (!metrics) return <span className="skill-metrics-empty">暂无使用指标</span>;
  const cells = [
    ["覆盖率", fmt(metrics.coverage_pct, "%")],
    ["近期覆盖", fmt(metrics.recent_coverage_pct, "%")],
    ["有效跟踪", fmt(Math.round(metrics.effective_tracking_seconds || 0), "s")],
    ["失联", fmt(Math.round(metrics.lost_seconds || 0), "s")],
    ["交接成功率", metrics.handoff_success_rate === null ? "—" : fmt(metrics.handoff_success_rate, "%")],
    ["使用中跟踪", fmt(metrics.tracking_contacts)],
  ];
  return (
    <div className="skill-metrics" aria-label={label}>
      {cells.map(([k, v]) => (
        <span key={k}><em>{k}</em>{v}</span>
      ))}
    </div>
  );
}

export default function SkillsPanel() {
  const [data, setData] = useState(null);
  const [selected, setSelected] = useState(null);
  const [detail, setDetail] = useState(null);
  const [sizeDraft, setSizeDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saveState, setSaveState] = useState(null);
  const editingRef = useRef(false);
  const timer = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const lib = await skillApi.list();
      setData(lib);
    } catch {
      setData((d) => d); // keep last good payload on transient errors
    }
  }, []);

  useEffect(() => {
    refresh();
    timer.current = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer.current);
  }, [refresh]);

  useEffect(() => {
    if (!selected) { setDetail(null); return undefined; }
    let alive = true;
    const load = async () => {
      if (editingRef.current) return; // don't clobber the editor
      try {
        const doc = await skillApi.get(selected);
        if (alive) setDetail(doc);
      } catch {
        if (alive) setDetail(null);
      }
    };
    setEditing(false);
    editingRef.current = false;
    setSaveState(null);
    load();
    const t = setInterval(load, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [selected]);

  const skills = data?.skills || [];
  const enabled = data?.enabled === true;
  const librarySize = data?.library_size;

  useEffect(() => {
    if (librarySize != null) setSizeDraft(String(librarySize));
  }, [librarySize]);

  const saveSize = async () => {
    const n = Number(sizeDraft);
    if (!Number.isFinite(n) || n < 1) return;
    try {
      await skillApi.setConfig({ library_size: n });
      refresh();
    } catch { /* disabled or invalid — panel shows current state */ }
  };

  return (
    <div className="skills-panel" role="region" aria-label="技能视图">
      <aside className="skill-library">
        <div className="skill-library-head">
          <strong>技能库</strong>
          <span className="plugin-count">{skills.length}</span>
          <button type="button" className="icon-btn" title="刷新" aria-label="刷新技能库" onClick={refresh}>
            <RefreshCw size={14} />
          </button>
        </div>
        {data && !enabled && (
          <p className="skill-disabled-note">技能凝练插件已关闭 — 技能凝练、查询与加载功能整体停用（在插件视图重新启用“技能凝练”后恢复）。</p>
        )}
        <ul className="skill-list">
          {skills.map((s) => {
            const id = s.slug || s.id;
            const active = selected === id;
            const conf = s.confidence;
            return (
              <li key={id}>
                <button
                  type="button"
                  className={`skill-card ${active ? "active" : ""}`}
                  onClick={() => setSelected(active ? null : id)}
                  aria-pressed={active}
                >
                  <span className="skill-card-top">
                    <strong>{s.title || s.name || id}</strong>
                    {s.category && <em className="skill-cat">{s.category}</em>}
                  </span>
                  <span className="skill-card-desc">{s.description}</span>
                  {typeof conf === "number" && (
                    <span className="skill-conf-mini" aria-label={`置信度 ${Math.round(conf * 100)}%`}>
                      <i style={{ width: `${Math.round(conf * 100)}%` }} />
                    </span>
                  )}
                </button>
              </li>
            );
          })}
          {data && skills.length <= 1 && enabled && (
            <li className="skill-empty">技能库为空 — 反思任务会按周期提炼调度轨迹，凝练结果将显示在这里。</li>
          )}
        </ul>
      </aside>

      <section className="skill-detail">
        {!selected ? (
          <div className="skill-detail-empty">点击左侧技能查看文档与使用状态</div>
        ) : !detail ? (
          <div className="skill-detail-empty">加载中…</div>
        ) : (
          <>
            <div className="skill-doc-bar">
              {!editing ? (
                <button type="button" onClick={() => { setDraft(detail.content || ""); setEditing(true); editingRef.current = true; }}>
                  编辑
                </button>
              ) : (
                <>
                  <button type="button" disabled={saveState === "saving"} onClick={async () => {
                    setSaveState("saving");
                    try {
                      await skillApi.save(selected, { content: draft });
                      setDetail((d) => d ? { ...d, content: draft } : d);
                      setSaveState("saved");
                      setEditing(false);
                      editingRef.current = false;
                    } catch {
                      setSaveState("error");
                    }
                  }}>
                    {saveState === "saving" ? "保存中…" : "保存"}
                  </button>
                  <button type="button" onClick={() => { setEditing(false); editingRef.current = false; setSaveState(null); }}>
                    取消
                  </button>
                  {saveState === "error" && <span className="skill-save-error">保存失败</span>}
                </>
              )}
              {saveState === "saved" && !editing && <span className="skill-save-ok">已保存</span>}
            </div>
            {editing ? (
              <textarea
                className="skill-doc-editor"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                aria-label="编辑 SKILL.md"
                spellCheck={false}
              />
            ) : (
              <article className="skill-doc">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{detail.content || "（无文档内容）"}</ReactMarkdown>
              </article>
            )}
            <footer className="skill-status">
              {detail.category !== "mission" && (
                <>
                  <div className="skill-stat-row">
                    <span className="skill-stat">
                      <em>置信度</em>
                      <span className="skill-conf-bar" role="progressbar"
                        aria-valuemin={0} aria-valuemax={100}
                        aria-valuenow={Math.round((detail.confidence ?? 0) * 100)}>
                        <i style={{ width: `${Math.round((detail.confidence ?? 0) * 100)}%` }} />
                      </span>
                      <b>{Math.round((detail.confidence ?? 0) * 100)}%</b>
                    </span>
                    <span className="skill-stat"><em>使用次数</em><b>{detail.uses ?? 0}</b></span>
                    <span className="skill-stat"><em>迭代版本</em><b>v{detail.version ?? 1}</b></span>
                    <span className="skill-stat"><em>UCB</em><b>{detail.ucb ?? "—"}</b></span>
                  </div>
                  <MetricLine label="最近一次使用的任务指标" metrics={detail.last_metrics} />
                </>
              )}
              <div className="skill-size-row">
                <em>技能库容量上限</em>
                <input
                  type="number" min={1} max={50} value={sizeDraft}
                  onChange={(e) => setSizeDraft(e.target.value)}
                  disabled={!enabled}
                  aria-label="技能库容量上限"
                />
                <button type="button" onClick={saveSize} disabled={!enabled || String(librarySize) === sizeDraft}>
                  保存
                </button>
                <span className="skill-size-hint">库满时按置信度最低淘汰，初始值由 configs 超参指定</span>
              </div>
            </footer>
          </>
        )}
      </section>
    </div>
  );
}
