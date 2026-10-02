"""Pre-registration review for agent tools (builtin and plugin-declared).

Pi tool definitions are a four-piece contract: prompt contribution
(snippet/guidelines), parameter schema, sampling constraint, execute body.
That alone is the low bar; the four production rules below decide whether
a tool is registerable. review() runs at agent_tools load for builtins and
inside plugins.install_plugin for TOOLS, so a tool that fails never
reaches the catalog.

Rules -> enforceable checks:

1. Failure is expressed by raising (throw = failure). A tool whose
   parameters declare required fields must raise when called with empty
   params; returning normally means it silently accepts invalid input.
2. Parallelism is declared, not assumed. execution_mode must be explicit
   ("sequential" | "parallel"); a tool running under the runtime lock
   (mode="lock" or any plugin tool) shares mission state and may not
   declare "parallel".
3. Shared-state mutation is queued. mode must be "lock" or "calculate";
   lock-mode execution serializes under runtime.lock — see api.invoke.
4. Usage is accounted. calls_model tools must return a result containing
   a "usage" key; resolve() wraps execute to enforce it at call time.
"""

_MISSING = object()

_VALID_MODES = ("sequential", "parallel")
_VALID_RUN_MODES = ("lock", "calculate")


class _ProbeRuntime:
    """Stand-in runtime for the failure-expression probe.

    Any attribute access raises, so an execute() that touches the runtime
    before validating its params still "raises" — only a normal return
    flags silent acceptance.
    """

    def __getattr__(self, name):
        raise AttributeError(f"probe runtime has no {name}")


_PROBE = _ProbeRuntime()


def _check_parameters(name, parameters, issues):
    """Structural sanity for the JSON-schema parameter dict."""
    if parameters.get("type") not in (None, "object"):
        issues.append(f"{name}: parameters.type must be 'object'")
    properties = parameters.get("properties", {})
    if not isinstance(properties, dict):
        issues.append(f"{name}: parameters.properties must be a dict")
        return
    for key, prop in properties.items():
        if not isinstance(prop, dict):
            issues.append(f"{name}: parameters.properties.{key} must be a schema dict")
    required = parameters.get("required", [])
    if not isinstance(required, list):
        issues.append(f"{name}: parameters.required must be a list")
    else:
        unknown = [key for key in required if key not in properties]
        if unknown:
            issues.append(
                f"{name}: parameters.required not in properties: {', '.join(unknown)}")


def review(name, spec, execute=None, plugin_tool=False):
    """Review one tool spec against the production rules.

    Returns a list of issue strings; an empty list means the tool may be
    registered. ``execute`` is the module-level body for builtins (the
    TOOL dict holds metadata only); plugin tools carry execute inside
    the spec. plugin_tool=True applies the plugin extra rules (name
    prefix is checked by the caller).
    """
    issues = []
    if not isinstance(spec, dict):
        return [f"{name}: tool spec must be a dict"]
    if execute is None:
        execute = spec.get("execute")

    for key in ("description", "snippet", "guidelines", "parameters",
                "execution_mode", "constrained_sampling"):
        if key not in spec:
            issues.append(f"{name}: missing required key '{key}'")
    if execute is None:
        issues.append(f"{name}: missing required key 'execute'")
    if issues:
        return issues

    if not isinstance(spec["description"], str) or not spec["description"].strip():
        issues.append(f"{name}: description must be a non-empty string")
    if not isinstance(spec["snippet"], str) or not spec["snippet"].strip():
        issues.append(f"{name}: snippet must be a non-empty string")
    guidelines = spec["guidelines"]
    if (not isinstance(guidelines, list) or not guidelines
            or not all(isinstance(g, str) and g.strip() for g in guidelines)):
        issues.append(f"{name}: guidelines must be a non-empty list of strings")
    if not isinstance(spec["parameters"], dict):
        issues.append(f"{name}: parameters must be a JSON-schema dict")
    else:
        _check_parameters(name, spec["parameters"], issues)
    if spec["execution_mode"] not in _VALID_MODES:
        issues.append(
            f"{name}: execution_mode must be explicit, one of {_VALID_MODES}")
    sampling = spec["constrained_sampling"]
    if (not isinstance(sampling, dict)
            or sampling.get("type") != "json_schema"
            or sampling.get("strict") not in ("prefer", "require")):
        issues.append(
            f"{name}: constrained_sampling must be "
            "{'type': 'json_schema', 'strict': 'prefer'|'require'}")
    if not callable(execute):
        issues.append(f"{name}: execute must be callable")

    run_mode = spec.get("mode", "lock" if plugin_tool else _MISSING)
    if run_mode is _MISSING:
        issues.append(f"{name}: missing required key 'mode'")
    elif run_mode not in _VALID_RUN_MODES:
        issues.append(f"{name}: mode must be one of {_VALID_RUN_MODES}")
    elif run_mode == "lock" and spec["execution_mode"] == "parallel":
        issues.append(
            f"{name}: lock-mode tools share mission state and must declare "
            "execution_mode 'sequential'")

    calls_model = spec.get("calls_model", False)
    if not isinstance(calls_model, bool):
        issues.append(f"{name}: calls_model must be a bool")
    if "errors" in spec and not (
            isinstance(spec["errors"], list)
            and all(isinstance(e, str) for e in spec["errors"])):
        issues.append(f"{name}: errors must be a list of strings when present")
    if issues:
        return issues

    required = spec["parameters"].get("required", [])
    if required:
        try:
            execute(_PROBE, {}, False)
        except Exception:
            pass
        else:
            issues.append(
                f"{name}: silent acceptance — execute() must raise on "
                "missing required params (throw = failure)")
    return issues


def accounting_wrapper(execute, calls_model):
    """Wrap execute for rule 4: a calls_model tool must return usage.

    Plugin/builtin code that internally calls a model is required to put
    token usage in the result or the session ledger lies.
    """
    if not calls_model:
        return execute

    def guarded(runtime, data, worker=False):
        result = execute(runtime, data, worker)
        if not isinstance(result, dict) or "usage" not in result:
            raise RuntimeError("tool_usage_missing: calls_model tools must return usage")
        return result
    return guarded
