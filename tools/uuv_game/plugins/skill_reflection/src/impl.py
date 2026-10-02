"""Skill reflection — LLM distillation of scheduling trajectories.

The plugin observes (never mutates) the pipeline: every ``window_s``
sim seconds its stage composes a reflection job — a digest of the
window's decision events plus the mission-metric delta — and enqueues
it for the PI agent via runtime.queue_agent. The agent's model does the
actual distillation inside a normal turn and commits results through
the ``skill_reflection__distill`` tool; ``skill_reflection__lookup`` is
the lazy-loading surface (the model pulls a skill's body only when the
current plan touches the matching functional goal).

Skill categories are fixed here — each category names one task-level
functional goal with its algorithm-slot cooperation chain and its own
reward weights, so "which plugin implemented it" never enters the
distilled knowledge.
"""

import json
import math
from . import bandit, store
from ....config import algorithm_settings


def _err(code, status=422):
    """MissionError factory — late-imported because runtime imports this
    plugins package at startup (runtime -> plugins -> impl cycle)."""
    from ....runtime import MissionError
    return MissionError(code, status)

# Category registry: one entry per task-related functional goal. The
# distill tool rejects categories outside this table, so a skill can
# only ever summarize a declared task-flow pattern.
CATEGORIES = {
    "target-dispatch": {"title": "目标发现资源调派",
        "goal": "首次确认目标后如何调派资源形成跟踪队",
        "chain": ["sensor_fusion", "task_allocation", "coop_tracking"],
        "weights": {"track": 0.5, "coverage": 0.2, "lost": 0.3, "handoff": 0.0}},
    "search-to-track": {"title": "覆盖搜索转入协同跟踪",
        "goal": "区域覆盖搜索中发现目标后向多UUV协同跟踪的流转",
        "chain": ["coverage_search", "sensor_fusion", "task_allocation", "coop_tracking"],
        "weights": {"track": 0.4, "coverage": 0.4, "lost": 0.2, "handoff": 0.0}},
    "track-handover": {"title": "跟踪任务轮换交接",
        "goal": "跟踪UUV能源不足返航时由接替者持续跟踪的流转",
        "chain": ["energy_lifecycle", "handover", "reacquire", "coop_tracking"],
        "weights": {"track": 0.4, "coverage": 0.0, "lost": 0.4, "handoff": 0.2}},
    "lost-reacquire": {"title": "目标丢失与重捕",
        "goal": "跟踪中断后重新覆盖搜索该区域并再次捕获目标",
        "chain": ["reacquire", "coverage_search", "sensor_fusion", "coop_tracking"],
        "weights": {"track": 0.3, "coverage": 0.3, "lost": 0.4, "handoff": 0.0}},
}

_TRACK_TYPES = ("tool_completed", "agent_plan_decided", "plan_submitted", "plan_approved",
    "plan_rejected", "plan_executed", "tracking_", "handover", "coverage_", "contact_",
    "skill_distilled", "plugin_hook_error")

_LAST_FIRE = {}
_LAST_SNAPSHOT = {}


def _settings():
    return algorithm_settings("skills")


def metric_snapshot(rt):
    """Compact metric vector used both as a use-time baseline and as the
    'after' state when settling a pending use (under runtime.lock)."""
    cell, height = rt.config.cell, rt.config.height
    margin = algorithm_settings("partition")["obstacle_margin_m"]
    searchable = [(c, r) for c in range(len(rt.scan_times)) for r in range(len(rt.scan_times[c]))
        if not any(math.hypot(max(c*cell, min(o["x"], (c+1)*cell))-o["x"],
                              max(height-(r+1)*cell, min(o["y"], height-r*cell))-o["y"]) <= o["radius"]+margin
                   for o in rt.obstacles)]
    total = max(1, len(searchable))
    seen = sum(rt.scan_times[c][r] >= 0 for c, r in searchable)
    window_s = rt.config.coverage_window_min * 60
    recent = sum(rt.scan_times[c][r] >= 0 and 0 <= rt.sim_time - rt.scan_times[c][r] <= window_s
                 for c, r in searchable)
    attempts = rt.metrics.get("handoff_attempts") or 0
    return {"sim_time": rt.sim_time,
            "coverage_pct": round(100*seen/total, 2),
            "recent_coverage_pct": round(100*recent/total, 2),
            "effective_tracking_seconds": rt.metrics.get("effective_tracking_seconds", 0.0),
            "lost_seconds": rt.metrics.get("lost_seconds", 0.0),
            "handoff_count": rt.metrics.get("handoff_count", 0),
            "handoff_attempts": attempts,
            "handoff_success_rate": round(100*rt.metrics.get("handoff_count", 0)/attempts, 2) if attempts else None,
            "tracking_contacts": sum(1 for c in rt.contacts.values() if c.get("state") == "tracking")}


def _weights(entry):
    return CATEGORIES.get(entry.get("category"), {}).get("weights") or {"track": 0.3, "coverage": 0.3, "lost": 0.4}


def settle(rt, reward_window_s=None):
    """Settle every pending use older than reward_window_s against the
    current snapshot — the multi-armed-bandit feedback step. Returns the
    number of uses settled."""
    meta = store.load()
    now = metric_snapshot(rt)
    window = reward_window_s if reward_window_s is not None else _settings()["reward_window_s"]
    settled = 0
    for entry in meta["skills"].values():
        pending = entry.get("pending", [])
        keep = []
        for use in pending:
            if now["sim_time"] - use["t"] >= window:
                gained = bandit.reward(use["snapshot"], now, _weights(entry), window)
                bandit.update(entry, gained)
                entry.setdefault("rewards", []).append({"t": now["sim_time"], "reward": gained})
                entry["rewards"] = entry["rewards"][-20:]
                settled += 1
            else:
                keep.append(use)
        entry["pending"] = keep
    if settled:
        store.save(meta)
    return settled


def _entry_summary(entry, meta):
    return {"slug": entry["slug"], "title": entry.get("title"), "category": entry.get("category"),
            "description": entry.get("description"), "confidence": entry.get("confidence"),
            "ucb": round(bandit.ucb(entry, store.total_uses(meta)), 4),
            "uses": entry.get("uses", 0), "version": entry.get("version", 1),
            "updated_at": entry.get("updated_at"), "last_metrics": entry.get("last_metrics")}


def library(rt=None):
    """Full library snapshot for /api/skills (settle skipped — no runtime
    needed; confidence reflects the last settled state)."""
    meta = store.load()
    settings = _settings()
    cap = meta.get("library_size") or settings["library_size"]
    skills = sorted((_entry_summary(e, meta) for e in meta["skills"].values()),
                    key=lambda s: -s["ucb"])
    return {"library_size": cap, "categories": [{"id": k, **{kk: v[kk] for kk in ("title", "goal", "chain")}} for k, v in CATEGORIES.items()],
            "skills": skills[:cap]}


def read_skill(slug):
    meta = store.load()
    entry = meta["skills"].get(slug)
    if entry is None:
        return None
    return {"slug": slug, **_entry_summary(entry, meta), "content": store.read_body(slug) or ""}


def set_library_size(n):
    meta = store.load()
    meta["library_size"] = max(1, min(50, int(n)))
    store.save(meta)
    return meta["library_size"]


def _find_slug(meta, name):
    key = store.slugify(name)
    if key and key in meta["skills"]:
        return key
    lowered = str(name).lower()
    for slug, entry in meta["skills"].items():
        if lowered in (entry.get("title") or "").lower() or lowered == slug:
            return slug
    return None


def skill_lookup(rt, params, worker=False):
    """Lazy-loading surface for the agent: empty -> ranked library
    headers (name, one-line description, confidence); a name -> the
    skill body plus its live metadata, and a use is recorded with the
    current metric baseline for later bandit feedback."""
    if not isinstance(params, dict):
        raise _err("invalid_params")
    settle(rt)
    meta = store.load()
    name = params.get("skill_name") or params.get("skill")
    if not name:
        return {"skills": library()["skills"], "library_size": library()["library_size"]}
    slug = _find_slug(meta, name)
    if slug is None:
        raise _err("skill_not_found", 404)
    entry = store.record_use(meta, slug, metric_snapshot(rt))
    return {**_entry_summary(entry, meta), "content": store.read_body(slug) or ""}


def skill_distill(rt, params, worker=False):
    """Commit one distilled skill (or refine it when the slug already
    exists — version+1). Category is validated against CATEGORIES so the
    library can only contain declared task-flow patterns."""
    if not isinstance(params, dict):
        raise _err("invalid_params")
    missing = [k for k in ("slug", "title", "category", "description", "body_md") if not params.get(k)]
    if missing:
        raise _err(f"skill_fields_required: {', '.join(missing)}")
    category = params["category"]
    if category not in CATEGORIES:
        raise _err("unknown_skill_category")
    slug = store.slugify(params["slug"])
    if not slug:
        raise _err("invalid_skill_slug")
    meta = store.load()
    cap = meta.get("library_size") or _settings()["library_size"]
    entry, refined, evicted = store.upsert(
        meta, slug=slug, title=str(params["title"])[:80], category=category,
        description=str(params["description"])[:160], body_md=str(params["body_md"])[:12000], cap=cap)
    rt.event("skill_distilled", {"slug": slug, "category": category, "version": entry["version"],
                                 "refined": refined, "evicted": evicted})
    rt.save()
    return {"slug": slug, "version": entry["version"], "refined": refined,
            "evicted": evicted, "library_size": cap, "category": category}


def _trajectory_digest(rt, window_s, max_chars):
    cutoff = rt.sim_time - window_s
    lines = []
    for e in rt.events:
        if e.get("time", 0) * 60 < cutoff or not any(e["type"].startswith(t) for t in _TRACK_TYPES):
            continue
        data = json.dumps(e.get("data", {}), ensure_ascii=False)[:140]
        lines.append(f"t+{int(e['time']*60 - cutoff)}s {e['type']} {data}")
    text = "\n".join(lines[-60:])
    return text[:max_chars]


def _compose_prompt(rt, window_s, delta, meta):
    settings = _settings()
    cats = "\n".join(f"- {key} {c['title']}：{c['goal']}（槽位链：{'→'.join(c['chain'])}）"
                     for key, c in CATEGORIES.items())
    existing = ", ".join(f"{e['slug']}@v{e['version']}(u{e['uses']},c{e['confidence']})"
                         for e in meta["skills"].values()) or "（空）"
    floor = settings["refine_confidence_floor"]
    weak = ", ".join(e["slug"] for e in meta["skills"].values()
                     if e.get("uses", 0) >= 1 and e.get("confidence", 0.5) < floor) or "无"
    return (
        "[技能凝练任务] 本次触发不是常规任务调度，不要提交任务计划。流程：\n"
        "1) 调用 load_plugin_tools 激活 skill_reflection__distill（必要时含 skill_reflection__lookup）。\n"
        "2) 依据下方时间窗内的调度轨迹与指标变化，提炼“功能目标→算法槽位协作链”的可复用经验；\n"
        "3) 仅当存在值得沉淀的模式时调用 skill_reflection__distill 提交（slug 相同视为 refine，版本+1）；无则回复一行“无可凝练”。\n"
        f"允许类目（category 必须在此列）：\n{cats}\n"
        f"指标变化（近{int(window_s)}s）：{delta}\n"
        f"时间窗轨迹：\n{_trajectory_digest(rt, window_s, settings['reflect_max_chars'] - 900)}\n"
        f"现有技能：{existing}\n低置信度待refine：{weak}"
    )[: _settings()["reflect_max_chars"]]


def reflect_stage(rt):
    """Pipeline stage appended after coverage_review: every window_s it
    settles pending bandit feedback and enqueues a reflection job for the
    agent. Observer only — never returns False."""
    settings = _settings()
    now = rt.sim_time
    if now - _LAST_FIRE.get(rt.episode, -1e9) < settings["window_s"]:
        return None
    _LAST_FIRE[rt.episode] = now
    settle(rt)
    current = metric_snapshot(rt)
    baseline = _LAST_SNAPSHOT.get(rt.episode)
    _LAST_SNAPSHOT[rt.episode] = current
    delta = "首个时间窗（无基线）" if baseline is None else (
        f"覆盖率{current['coverage_pct']-baseline['coverage_pct']:+.1f}%，"
        f"近期覆盖{current['recent_coverage_pct']-baseline['recent_coverage_pct']:+.1f}%，"
        f"有效跟踪{current['effective_tracking_seconds']-baseline['effective_tracking_seconds']:+.0f}s，"
        f"失联{current['lost_seconds']-baseline['lost_seconds']:+.0f}s")
    rt.queue_agent(_compose_prompt(rt, settings["window_s"], delta, store.load()), source="skill_reflection")
    return None


def activity(runtime, ctx):
    library_size = len(store.load()["skills"])
    if library_size:
        ctx.meta(f"技能库 {library_size} 条")
    queued = sum(1 for j in runtime.agent_jobs if j["status"] in ("queued", "running") and j["source"] == "skill_reflection")
    if queued:
        ctx.hit()
        ctx.meta("反思任务进行中")
    return None


def tick_stages(_runtime):
    return [{"after": "coverage_review", "fn": reflect_stage}]
