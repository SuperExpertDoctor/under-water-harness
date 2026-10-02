"""Distilled-skill store — markdown bodies + index.json metadata.

Skills live under ``tools/skills/`` (the algorithm directory): one
``<slug>/SKILL.md`` folder per skill plus ``index.json`` holding every
skill's bandit metadata (confidence posterior, use count, version,
per-use metric baselines, reward history). Two ways in, same surface:
manual — drop a ``<slug>/SKILL.md`` folder (an optional ``key: value``
frontmatter block sets title/category/description); automatic — the
``skill_reflection__distill`` tool writes the same layout. index.json
is also where a UI-set library_size override persists.

Initial loading follows ``algorithms.skills.load_mode`` in
configs/uuv_game.json: ``"scan"`` registers every SKILL.md folder found;
``"manual"`` registers only slugs listed in ``manual_skills``.
"""

import json
import os
import re
import time
from pathlib import Path

from ....config import algorithm_settings

SKILLS_DIR = Path(os.environ.get("UUV_SKILLS_DIR", "") or Path(__file__).resolve().parents[4] / "skills")
_INDEX = "index.json"


def _index_path():
    return SKILLS_DIR / _INDEX


def md_path(slug):
    return SKILLS_DIR / slug / "SKILL.md"


def _frontmatter(text):
    """Parse a minimal `key: value` header block at the top of a manually
    added SKILL.md (title / category / description). Lines keep their
    place in the body — only a leading --- ... --- block is consumed."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text or "", re.S)
    if not match:
        return {}, text
    fields = {}
    for line in match.group(1).splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip() in ("title", "category", "description"):
            fields[key.strip()] = value.strip()
    return fields, text[match.end():].lstrip("\n")


def _scan_folders():
    """slugs of folders holding a SKILL.md, or None when the dir is absent."""
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p.name for p in SKILLS_DIR.iterdir()
                  if p.is_dir() and (p / "SKILL.md").is_file())


def _allowed_slugs():
    """None = scan everything; a set = manual allowlist from configs."""
    settings = algorithm_settings("skills")
    if settings.get("load_mode", "scan") != "manual":
        return None
    return {slugify(s) for s in settings.get("manual_skills", []) if slugify(s)}


def load():
    """{version, library_size?, skills: {slug: meta}} — never raises.

    Registers manual SKILL.md folders missing from the index and applies
    the configured load_mode gate (manual = index entries restricted to
    manual_skills). Registration is cheap: meta stays in index.json, the
    body is only read on demand."""
    try:
        data = json.loads(_index_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
            data = None
    except Exception:
        data = None
    if data is None:
        data = {"version": 1, "library_size": None, "skills": {}}
    skills = data["skills"]
    allowed = _allowed_slugs()
    changed = False
    for entry in skills.values():
        if "source" not in entry:
            entry["source"] = "distilled"
            changed = True
    for slug in _scan_folders():
        if allowed is not None and slug not in allowed:
            continue
        if slug in skills:
            continue
        fields, _body = _frontmatter(_raw_body(slug) or "")
        title = fields.get("title") or slug.replace("-", " ").title()
        entry = {"slug": slug, "alpha": 1.0, "beta": 1.0, "confidence": 0.5,
                 "uses": 0, "pending": [], "rewards": [], "version": 1,
                 "title": title, "category": fields.get("category"),
                 "description": fields.get("description") or "",
                 "source": "manual", "updated_at": time.time()}
        skills[slug] = entry
        changed = True
    if allowed is not None:
        for slug in [s for s in list(skills) if s not in allowed]:
            skills.pop(slug)
            changed = True
    if changed:
        save(data)
    return data


def save(meta):
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _index_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_index_path())


def slugify(name):
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    return slug[:48] or None


def _raw_body(slug):
    path = md_path(slug)
    return path.read_text(encoding="utf-8") if path.exists() else None


def read_body(slug):
    """Body for display — a leading frontmatter block (manual entries)
    is metadata, not content, so it is stripped here."""
    raw = _raw_body(slug)
    if raw is None:
        return None
    return _frontmatter(raw)[1]


def write_body(slug, body):
    path = md_path(slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


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
            md_path(evicted).parent.rmdir()
        entry = {"slug": slug, "alpha": 1.0, "beta": 1.0, "confidence": 0.5,
                 "uses": 0, "pending": [], "rewards": [], "version": 1,
                 "title": title, "category": category, "description": description,
                 "source": "distilled"}
    entry["updated_at"] = time.time()
    entry["slug"] = slug
    skills[slug] = entry
    md_path(slug).parent.mkdir(parents=True, exist_ok=True)
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
