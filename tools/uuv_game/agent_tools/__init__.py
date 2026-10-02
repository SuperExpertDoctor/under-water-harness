"""Agent tool registry — builtin tool modules plus plugin-declared TOOLS.

Mirrors the plugins loader: every ``*.py`` here (not ``_``-prefixed, not
``contract``) that defines a ``TOOL`` dict and an ``execute`` function is
registered by tool name. Plugin-declared tools live in the plugins
registry and merge into the same catalog/dispatch — dropping a plugin
file into ``plugins/`` is enough to hand the agent a new callable tool.
"""

import importlib
import pkgutil

from .review import accounting_wrapper, review

_MODULES = {}


def _load():
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda i: i.name):
        if info.name.startswith("_") or info.name in ("contract", "review"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        spec = getattr(module, "TOOL", None)
        if isinstance(spec, dict) and spec.get("name"):
            issues = review(spec["name"], spec, execute=module.execute
                            if callable(getattr(module, "execute", None)) else None)
            if issues:
                raise ValueError(
                    f"builtin tool failed review: {'; '.join(issues)}")
            _MODULES[spec["name"]] = module


_load()


def _plugin_specs():
    from ..plugins import plugin_tool_specs  # late import: plugins -> agent_tools is one-way
    return plugin_tool_specs()


def spec(name):
    """Spec dict for a tool name, builtin or plugin-declared (or None)."""
    module = _MODULES.get(name)
    if module is not None:
        return module.TOOL
    return _plugin_specs().get(name)


def names():
    return sorted(set(_MODULES) | set(_plugin_specs()))


def is_builtin(name):
    return name in _MODULES


def resolve(name):
    """(mode, execute) for a tool name; (None, None) when unknown.

    mode is "calculate" (run off the lock in a worker thread) or "lock"
    (run under runtime.lock). Plugin-declared tools always run "lock".
    """
    module = _MODULES.get(name)
    if module is not None:
        return (module.TOOL.get("mode", "lock"),
                accounting_wrapper(module.execute, module.TOOL.get("calls_model", False)))
    entry = _plugin_specs().get(name)
    if entry is not None:
        return ("lock", accounting_wrapper(
            entry["execute"], entry.get("calls_model", False)))
    return None, None


def catalog():
    """JSON-serializable tool descriptors for the agent-side adapter."""
    tools = []
    for name in names():
        entry = {key: value for key, value in spec(name).items()
                 if key not in ("mode", "execute")}
        entry.setdefault("name", name)
        entry.setdefault("plugin", name in _plugin_specs())
        entry.setdefault("execution_mode", "parallel")
        entry.setdefault("constrained_sampling",
                         {"type": "json_schema", "strict": "prefer"})
        tools.append(entry)
    return {"tools": tools}
