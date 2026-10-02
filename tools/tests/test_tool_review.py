"""Registration review for agent tools (agent_tools/review.py)."""

import pytest

from uuv_game.agent_tools import catalog, resolve
from uuv_game.agent_tools.review import review
from uuv_game.plugins import install_plugin, uninstall_plugin


BASE = {
    "description": "探测一艘 UUV",
    "snippet": "Probe one UUV",
    "guidelines": ["只在需要单艇信息时调用"],
    "parameters": {
        "type": "object",
        "properties": {"uuv_id": {"type": "string", "minLength": 1}},
        "required": ["uuv_id"],
    },
    "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
    "execution_mode": "sequential",
}


def execute(runtime, params, worker=False):
    if not params.get("uuv_id"):
        raise ValueError("uuv_id_required")
    return {"uuv_id": params["uuv_id"], "ok": True}


def valid_spec(**overrides):
    spec = {**BASE, "execute": execute}
    spec.update(overrides)
    return spec


def test_builtin_tools_all_pass_review():
    for entry in catalog()["tools"]:
        assert entry["execution_mode"] in ("sequential", "parallel")
        assert entry["constrained_sampling"]["strict"] in ("prefer", "require")
        assert entry["guidelines"]


def test_valid_spec_passes():
    assert review("plug__probe", valid_spec(), plugin_tool=True) == []


def test_missing_required_keys_fail():
    issues = review("plug__probe", {"description": "x"}, plugin_tool=True)
    assert any("snippet" in issue for issue in issues)
    assert any("execution_mode" in issue for issue in issues)
    assert any("constrained_sampling" in issue for issue in issues)
    assert any("execute" in issue for issue in issues)


def test_explicit_parallelism_required():
    spec = valid_spec()
    del spec["execution_mode"]
    assert review("plug__probe", spec, plugin_tool=True)


def test_plugin_tools_must_not_declare_parallel():
    # Plugin tools run under runtime.lock — shared state is queued, not raced.
    issues = review("plug__probe", valid_spec(execution_mode="parallel"),
                    plugin_tool=True)
    assert any("sequential" in issue for issue in issues)


def test_silent_acceptance_fails_rule_one():
    def silent(runtime, params, worker=False):
        return {"ok": True}
    issues = review("plug__probe", valid_spec(execute=silent), plugin_tool=True)
    assert any("silent" in issue for issue in issues)


def test_schema_required_must_exist_in_properties():
    spec = valid_spec()
    spec["parameters"] = {"type": "object", "properties": {},
                          "required": ["uuv_id"]}
    assert review("plug__probe", spec, plugin_tool=True)


def test_calls_model_requires_usage_in_result():
    def modeled(runtime, params, worker=False):
        return {"ok": True}
    spec = valid_spec(calls_model=True)
    spec["execute"] = modeled
    spec["parameters"] = {"type": "object", "properties": {}}
    assert review("plug__probe", spec, plugin_tool=True) == []
    # Enforcement happens at resolve() time via the accounting wrapper.
    from uuv_game.agent_tools.review import accounting_wrapper
    with pytest.raises(RuntimeError, match="tool_usage_missing"):
        accounting_wrapper(modeled, True)(None, {})


def test_plugin_install_rejects_unreviewed_tools(tmp_path):
    bad_tool = (
        "        'description': 'x',\n"
        "        'parameters': {},\n"
        "        'execute': _probe,\n")
    source = (
        "PLUGIN = {'id': 'rev-test', 'name': 'rev test', 'layer': 2,\n"
        "          'color': '#fff', 'desc': 'd', 'inputs': 'none',\n"
        "          'outputs': 'none'}\n"
        "def _probe(runtime, params, worker=False):\n"
        "    return {}\n"
        "TOOLS = {\n"
        "    'rev_test__probe': {\n" + bad_tool + "    },\n"
        "}\n")
    with pytest.raises(ValueError, match="failed registration review"):
        install_plugin(source)


def test_plugin_install_accepts_reviewed_tools():
    source = (
        "PLUGIN = {'id': 'rev-good', 'name': 'rev good', 'layer': 2,\n"
        "          'color': '#fff', 'desc': 'd', 'inputs': 'none',\n"
        "          'outputs': 'none'}\n"
        "def _probe(runtime, params, worker=False):\n"
        "    if not params.get('uuv_id'):\n"
        "        raise ValueError('uuv_id_required')\n"
        "    return {'uuv_id': params['uuv_id']}\n"
        "TOOLS = {\n"
        "    'rev_good__probe': {\n"
        "        'description': '探测一艘 UUV',\n"
        "        'snippet': 'Probe one UUV',\n"
        "        'guidelines': ['只在需要单艇信息时调用'],\n"
        "        'parameters': {'type': 'object', 'properties': {\n"
        "            'uuv_id': {'type': 'string'}}, 'required': ['uuv_id']},\n"
        "        'constrained_sampling': {'type': 'json_schema', 'strict': 'prefer'},\n"
        "        'execution_mode': 'sequential',\n"
        "        'execute': _probe,\n"
        "    },\n"
        "}\n")
    try:
        install_plugin(source)
        mode, fn = resolve("rev_good__probe")
        assert mode == "lock"
        assert fn(None, {"uuv_id": "UUV-1"}) == {"uuv_id": "UUV-1"}
        assert "rev_good__probe" in {t["name"] for t in catalog()["tools"]}
    finally:
        uninstall_plugin("rev-good")
    assert resolve("rev_good__probe") == (None, None)
