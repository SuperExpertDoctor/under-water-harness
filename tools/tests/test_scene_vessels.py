import json

import pytest
from fastapi.testclient import TestClient

from uuv_game.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "mission.sqlite", worker_token="test-worker", ticking=False)) as client:
        client.get("/api/health")
        yield client


@pytest.mark.parametrize("method,path", [
    ("POST", "/api/vessels"), ("DELETE", "/api/vessels/TARGET-1"), ("PATCH", "/api/vessels/TARGET-1/ais"),
])
def test_single_target_game_disables_scene_and_ais_mutations(client, method, path):
    runtime = client.app.state.runtime
    response = client.request(method, path, json={"episode_id": runtime.episode, "command_id": "locked-scene",
        "vessel_class": "type_ii", "position_cells": [25, 10], "ais_enabled": True})
    assert response.status_code == 409
    assert response.json()["error_code"] == "single_target_scene_locked"
    assert len(runtime.targets) == 1
    assert len(runtime.uuvs) == 8
    assert client.get("/api/scene").json()["scenario_vessels"] == []
    assert client.get("/api/scene").json()["vessel_mutation_allowed"] is False
    assert client.get("/api/state").json()["vessel_mutation_allowed"] is False


def test_hidden_target_has_no_ais_bypass(client):
    runtime = client.app.state.runtime
    runtime.targets[0].update(ais_enabled=True, vessel_class="type_ii", ais_mmsi="900000001")
    runtime.sensor_enabled = False
    runtime.start()
    for _ in range(15):
        runtime.tick()
    assert runtime.mission_state()["contacts"] == []
    assert "targets" not in runtime.mission_state()
    assert runtime.frame()["scenario_vessels"] == []


def test_tool_timeline_contains_allowlisted_parameters_and_decision_receipts(client):
    runtime = client.app.state.runtime
    runtime.set_mode("request")
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    runtime.queue_agent("Evaluate candidate")
    headers = {"Authorization": "Bearer test-worker"}
    job = client.post("/internal/agent/next", json={}, headers=headers).json()["job"]
    params = {"run_id": job["run_id"], "episode_id": runtime.episode, "result_id": candidate["result_id"],
        "api_key": "not-a-real-secret", "targets": [{"pose": [1, 2, 3]}]}
    response = client.post("/internal/tools/evaluate_plan", json=params, headers=headers)
    response.raise_for_status()
    started = next(event for event in reversed(runtime.events) if event["type"] == "tool_started")
    completed = next(event for event in reversed(runtime.events) if event["type"] == "tool_completed")
    assert started["data"]["params"] == {"result_id": candidate["result_id"]}
    assert completed["data"]["valid"] is True
    assert completed["data"]["errors"] == []
    assert completed["data"]["requires_approval"] is True
    assert completed["data"]["risk"] == .2
    assert "not-a-real-secret" not in json.dumps(runtime.events)
    response = client.post("/internal/tools/submit_mission_plan", json={**params, "command_id": "scene-timeline"}, headers=headers)
    response.raise_for_status()
    completed = next(event for event in reversed(runtime.events) if event["type"] == "tool_completed")
    assert completed["data"]["plan_id"] == response.json()["plan_id"]
    assert completed["data"]["members"] == ["UUV-1"]
    assert completed["data"]["status"] == "pending_approval"
