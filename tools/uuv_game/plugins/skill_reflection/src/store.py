"""Distilled-skill store — markdown bodies + index.json metadata.

Skills live under outputs/runtime/skills/ (shared across runs, like
credentials.env): one <slug>.md per skill plus index.json holding every
skill's bandit metadata (confidence posterior, use count, version,
per-use metric baselines, reward history). index.json is also where a
UI-set library_size override persists.
"""

import json
import os
import re
import time
from pathlib import Path

SKILLS_DIR = Path(os.environ.get("UUV_SKILLS_DIR", "") or Path(__file__).resolve().parents[5] / "outputs" / "runtime" / "skills")
_INDEX = "index.json"


def _index_path():
    return SKILLS_DIR / _INDEX


def load():
    """{version, library_size?, skills: {slug: meta}} — never raises."""
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("skills"), dict):
            return data
    except Exception:
        pass
    return {"version": 1, "library_size": None, "skills": {}}


def save(meta):
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_index_path())


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return slug[:48] or None


def md_path(slug):
    return SKILLS_DIR / f"{slug}.md"


def read_body(slug):
    path = md_path(slug)
    return path.read_text(encoding="utf-8") if path.exists() else None


def upsert(meta, *, slug, title, category, description, body_md, cap):
    """Create a skill or refine an existing one (same slug = refine, version+1).

    Returns (entry, refined, evicted_slug). Evicts the weakest skill when
    the library is at capacity: lowest confidence, then oldest update.
    """
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skills = meta["skills"]
    existing = skills.get(slug)
    refined = existing is not None
    if refined:
        entry = existing
        entry.update(title=title, category=category, description=description,
                     version=int(entry.get("version", 1)) + 1)
    else:
        evicted = None
        if len(skills) >= cap:
            victim = min(skills.values(), key=lambda s: (s.get("confidence", 0.5), s.get("updated_at", 0)))
            evicted = victim["slug"]
            skills.pop(evicted, None)
            md_path(evicted).unlink(missing_ok=True)
        entry = {"slug": slug, "alpha": 1.0, "beta": 1.0, "confidence": 0.5,
                 "uses": 0, "pending": [], "rewards": [], "version": 1,
                 "title": title, "category": category, "description": description}
    entry["updated_at"] = time.time()
    entry["slug"] = slug
    skills[slug] = entry
    md_path(slug).write_text(body_md, encoding="utf-8")
    save(meta)
    return entry, refined, (evicted if not refined else None)


def record_use(meta, slug, snapshot):
    entry = meta["skills"].get(slug)
    if entry is None:
        return None
    entry["uses"] = int(entry.get("uses", 0)) + 1
    entry["last_used_s"] = snapshot["sim_time"]
    entry["last_metrics"] = snapshot
    entry.setdefault("pending", []).append({"t": snapshot["sim_time"], "snapshot": snapshot})
    entry["pending"] = entry["pending"][-8:]
    save(meta)
    return entry


def total_uses(meta):
    return sum(int(s.get("uses", 0)) for s in meta["skills"].values())
