"""Plugin registry and drop-in loader for the UUV control stack.

Every ``*.py`` file in this directory whose name does not start with ``_``
is imported once; modules that define a ``PLUGIN`` dict are registered.
The contract is documented in ``contract.py`` and ``_template.py``.

Public surface (kept import-compatible with the old plugins.py module):
    PLUGIN_SPECS        discovered spec dicts, sorted by (layer, id)
    PLUGIN_EDGES        merged edge declarations
    catalog(enabled)    plugin catalog incl. live enabled flags
    frame_activity(rt)  per-frame plugin invocations for the connection graph
    stage_owners(slot)  plugin ids owning a named pipeline slot
    custom_stages(rt)   stage entries contributed by non-core plugins
"""

import importlib
import pkgutil
import re
import sys
import types
from pathlib import Path

from .contract import STAGE_SLOTS

_PACKAGE_DIR = Path(__file__).parent
_MODULES = {}


def _registrable(info):
    return not info.name.startswith("_") and info.name != "contract"


def _load():
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if not _registrable(info):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        spec = getattr(module, "PLUGIN", None)
        if isinstance(spec, dict) and spec.get("id"):
            _MODULES[spec["id"]] = module


_load()

def _edge_dict(entry):
    if isinstance(entry, dict):
        return dict(entry)
    return {"from": entry[0], "to": entry[1]}


PLUGIN_SPECS = sorted(
    ({**m.PLUGIN, "edges": [_edge_dict(e) for e in m.PLUGIN.get("edges", ())]}
     for m in _MODULES.values()),
    key=lambda s: (s.get("layer", 99), s["id"]),
)

PLUGIN_EDGES = [_edge_dict(e) for m in _MODULES.values()
                for e in m.PLUGIN.get("edges", ())]

_EDGE_SUBJECT_FNS = {}
_BUILTIN_STAGE_FNS = {}
for _module in _MODULES.values():
    _EDGE_SUBJECT_FNS.update(getattr(_module, "EDGE_SUBJECTS", {}) or {})
    if _module.PLUGIN.get("core"):
        _BUILTIN_STAGE_FNS.update(getattr(_module, "STAGES", {}) or {})


def reload_plugins():
    """Re-scan this directory: pick up added/edited/deleted plugin files."""
    for pid, module in list(_MODULES.items()):
        name = module.__name__.rsplit(".", 1)[-1]
        if not (_PACKAGE_DIR / f"{name}.py").exists():
            _MODULES.pop(pid)
            sys.modules.pop(module.__name__, None)
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if not _registrable(info):
            continue
        existing = next((m for m in _MODULES.values()
                        if m.__name__ == f"{__name__}.{info.name}"), None)
        if existing is not None:
            try:
                module = importlib.reload(existing)
            except Exception:
                module = existing  # broken edit: keep the last good module
        else:
            module = importlib.import_module(f"{__name__}.{info.name}")
        spec = getattr(module, "PLUGIN", None)
        if isinstance(spec, dict) and spec.get("id"):
            _MODULES[spec["id"]] = module
    stale = [pid for pid, m in _MODULES.items()
             if m.__name__ not in sys.modules or m.PLUGIN.get("id") != pid]
    for pid in stale:
        _MODULES.pop(pid)
    _rebuild()
    return [m.PLUGIN["id"] for m in _MODULES.values()]


def _rebuild():
    # Mutate in place: modules that imported PLUGIN_SPECS/PLUGIN_EDGES keep a
    # live view across reloads.
    PLUGIN_SPECS[:] = sorted(
        ({**m.PLUGIN, "edges": [_edge_dict(e) for e in m.PLUGIN.get("edges", ())]}
         for m in _MODULES.values()),
        key=lambda s: (s.get("layer", 99), s["id"]),
    )
    PLUGIN_EDGES[:] = [_edge_dict(e) for m in _MODULES.values()
                       for e in m.PLUGIN.get("edges", ())]
    _EDGE_SUBJECT_FNS.clear()
    _BUILTIN_STAGE_FNS.clear()
    for m in _MODULES.values():
        _EDGE_SUBJECT_FNS.update(getattr(m, "EDGE_SUBJECTS", {}) or {})
        # Only core plugins may provide named-slot implementations; custom
        # plugins insert positioned stages via tick_stages() instead.
        if m.PLUGIN.get("core"):
            _BUILTIN_STAGE_FNS.update(getattr(m, "STAGES", {}) or {})


_REQUIRED_KEYS = ("id", "name", "layer", "color", "desc", "inputs", "outputs")


def install_plugin(source):
    """Validate a plugin source file and drop it into this directory.

    Returns the registered spec dict. Raises ValueError on any contract
    violation; the file is only written after validation succeeds.
    """
    probe = types.ModuleType("uuv_game_plugin_probe")
    try:
        exec(compile(source, "<plugin>", "exec"), probe.__dict__)
    except Exception as exc:
        raise ValueError(f"plugin source failed to load: {exc}") from exc
    spec = getattr(probe, "PLUGIN", None)
    if not isinstance(spec, dict):
        raise ValueError("module must define a PLUGIN dict")
    missing = [key for key in _REQUIRED_KEYS if key not in spec]
    if missing:
        raise ValueError(f"PLUGIN missing required keys: {', '.join(missing)}")
    pid = spec["id"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", pid):
        raise ValueError("PLUGIN id must be kebab-case [a-z0-9-]")
    if pid in _MODULES:
        raise ValueError(f"plugin id already registered: {pid}")
    if spec.get("core"):
        raise ValueError("custom plugins cannot claim core status")
    for slot in spec.get("owns_stages", ()):
        if slot not in STAGE_SLOTS:
            raise ValueError(f"unknown pipeline slot: {slot}")
    filename = pid.replace("-", "_")
    path = _PACKAGE_DIR / f"{filename}.py"
    if path.exists():
        raise ValueError(f"plugin file already exists: {path.name}")
    path.write_text(source, encoding="utf-8")
    reload_plugins()
    return next(s for s in PLUGIN_SPECS if s["id"] == pid)


def uninstall_plugin(plugin_id):
    """Remove a non-core plugin's file and unregister it."""
    module = _MODULES.get(plugin_id)
    if module is None:
        return False
    if module.PLUGIN.get("core"):
        raise ValueError("core plugins cannot be removed")
    name = module.__name__.rsplit(".", 1)[-1]
    path = _PACKAGE_DIR / f"{name}.py"
    if path.exists():
        path.unlink()
    _MODULES.pop(plugin_id)
    sys.modules.pop(module.__name__, None)
    _rebuild()
    return True


def stage_impl(slot):
    """Stage implementation fn(rt) provided by a builtin plugin, if any."""
    return _BUILTIN_STAGE_FNS.get(slot)


def catalog(enabled=None):
    states = enabled or {}
    return {"plugins": [{**spec, "enabled": states.get(spec["id"], True)}
                        for spec in PLUGIN_SPECS],
            "edges": PLUGIN_EDGES}


def stage_owners(slot):
    """Plugin ids that own the named pipeline slot (empty = always runs)."""
    return [spec["id"] for spec in PLUGIN_SPECS if slot in spec.get("owns_stages", ())]


def custom_stages(runtime):
    """Stage entries contributed by non-core plugins (see contract)."""
    extra = []
    for module in _MODULES.values():
        if module.PLUGIN.get("core"):
            continue
        hook = getattr(module, "tick_stages", None)
        if hook is None:
            continue
        for entry in hook(runtime) or []:
            extra.append({"after": entry.get("after"), "fn": entry["fn"],
                          "plugin": module.PLUGIN["id"]})
    return extra


def merge_stages(builtin, extra):
    """Merge (slot, fn) builtin slots with custom {after, fn} entries."""
    pipeline = list(builtin)
    for entry in extra:
        index = len(pipeline)
        if entry["after"]:
            index = next((i + 1 for i, (slot, _fn) in enumerate(pipeline)
                          if slot == entry["after"]), len(pipeline))
        pipeline.insert(index, (f"plugin:{entry['plugin']}", entry["fn"]))
    return pipeline


class _FrameCtx:
    """Per-frame context handed to each plugin's activity() hook."""

    def __init__(self, plugin_id, node, lists):
        self._plugin_id = plugin_id
        self._node = node
        self.L = lists

    def hit(self, subject=None):
        self._node["active"] = True
        if subject and subject not in self._node["subjects"]:
            self._node["subjects"].append(subject)

    def meta(self, text):
        self._node["meta"] = text


def _base_lists(runtime):
    searching, transit_track, transit_other, tracking, exiting, relief = [], [], [], [], [], []
    moving = []
    for uid, action in runtime.active.items():
        kind, phase = action.get("kind"), action.get("phase")
        if kind == "search":
            searching.append(uid)
        elif kind == "reacquire":
            searching.append(uid)
        elif kind == "exit":
            exiting.append(uid)
        elif kind == "track":
            (transit_track if phase == "transit" else tracking).append(uid)
        else:
            transit_other.append(uid)
        if action.get("relief_member"):
            relief.append(uid)
        moving.append(uid)
    low_fuel = [u["id"] for u in runtime.uuvs
                if u["remaining_range_m"] <= 0.2 * runtime.config.range_capacity and u["id"] not in exiting]
    contacts = [{"contact_id": cid, **c} for cid, c in runtime.contacts.items()]
    tracked = [c for c in contacts if c.get("state") in ("tracking", "degraded")]
    lost = [c for c in contacts if c.get("state") == "lost"]
    observers = sorted({o for c in tracked for o in c.get("observers", [])})
    plans = [p for p in runtime.plans.values() if p.get("status") == "active"]
    allocated = sorted({m for p in plans for m in p.get("active_members", p.get("members", []))})
    reacquire_members = sorted({m for p in plans if p.get("kind") == "reacquire"
                                for m in p.get("active_members", p.get("members", []))})
    owners = sorted(r["owner"] for r in runtime.regions if r.get("owner"))
    return {"searching": searching, "transit_track": transit_track, "transit_other": transit_other,
            "tracking": tracking, "exiting": exiting, "relief": relief, "moving": moving,
            "low_fuel": low_fuel, "contacts": contacts, "tracked": tracked, "lost": lost,
            "observers": observers, "plans": plans, "allocated": allocated,
            "reacquire_members": reacquire_members, "owners": owners}


def frame_activity(runtime):
    """Per-frame plugin invocations derived from the runtime's own state.

    runtime.active is the dispatch table; each registered plugin decides for
    itself (via its activity() hook) which invocations belong to it.
    """
    states = runtime.plugin_states
    nodes = {spec["id"]: {"active": False, "subjects": [], "meta": None,
                          "enabled": states.get(spec["id"], True)}
             for spec in PLUGIN_SPECS}
    lists = _base_lists(runtime)

    for spec in PLUGIN_SPECS:
        if not states.get(spec["id"], True):
            continue
        hook = getattr(_MODULES[spec["id"]], "activity", None)
        if hook is not None:
            hook(runtime, _FrameCtx(spec["id"], nodes[spec["id"]], lists))

    edges = []
    for edge in PLUGIN_EDGES:
        key = f"{edge['from']}>{edge['to']}"
        fn = _EDGE_SUBJECT_FNS.get(key)
        subjects = fn(lists) if fn else []
        active = (nodes.get(edge["from"], {}).get("active")
                  and nodes.get(edge["to"], {}).get("active")
                  and (subjects or edge.get("always_active")))
        edges.append({"key": key, "from": edge["from"], "to": edge["to"],
                      "active": bool(active), "subjects": subjects})

    return {"nodes": nodes, "edges": edges,
            "step": runtime.frame_id, "sim_min": runtime.sim_time / 60}
