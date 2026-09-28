from fastapi.testclient import TestClient
import pytest

from uuv_game.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "mission.sqlite", worker_token="test-worker", ticking=False)) as client:
        client.get("/api/health")
        yield client


def test_state_and_websocket(client):
    state = client.get("/api/state").json()
    assert len(state["uavs"]) == 8
    with client.websocket_connect("/ws/live") as ws:
        assert ws.receive_json()["episode_id"] == state["episode_id"]


def test_controls_and_authentication(client):
    episode = client.get("/api/state").json()["episode_id"]
    assert client.post("/api/simulation/start", json={"episode_id": episode}).status_code == 200
    assert client.post("/api/simulation/pause", json={"episode_id": "old"}).status_code == 409
    client.cookies.clear()
    assert client.post("/api/simulation/reset", json={"episode_id": episode}).status_code == 403


def test_internal_tools_require_distinct_worker_token(client):
    assert client.post("/internal/tools/get_mission_state", json={}).status_code == 403
    headers = {"Authorization": "Bearer test-worker"}
    episode = client.get("/api/state").json()["episode_id"]
    client.post("/api/pi-agent/messages", json={"episode_id": episode, "text": "Observe only"})
    job = client.post("/internal/agent/next", json={}, headers=headers).json()["job"]
    metadata = {"episode_id": episode, "run_id": job["run_id"]}
    response = client.post("/internal/tools/get_mission_state", json=metadata, headers=headers)
    assert response.status_code == 200
    assert "targets" not in response.json()
    assert client.post("/internal/tools/set_mode", json=metadata, headers=headers).status_code == 404


def test_documented_endpoint_groups(client):
    for endpoint in ("/api/config", "/api/events", "/api/pi-agent/status", "/api/pi-agent/task-assignment", "/api/task-assembly/tasks", "/api/skills", "/api/algorithm/status", "/api/replay/list", "/api/export/capabilities", "/api/approvals"):
        assert client.get(endpoint).status_code == 200, endpoint
    assert client.get("/api/replay", params={"file": "../../etc/passwd"}).status_code == 404


def test_scene_and_intent_receipts(client):
    episode = client.get("/api/state").json()["episode_id"]
    payload = {"episode_id": episode, "command_id": "scene-1", "vessel_class": "type_ii", "position_cells": [10, 10]}
    response = client.post("/api/vessels", json=payload)
    assert response.status_code == 409
    assert response.json()["error_code"] == "single_target_scene_locked"
    assert client.get("/api/vessel-commands/scene-1").status_code == 404
    assert client.post("/api/vessels", json=payload).json() == response.json()
    assert client.get("/api/state").json()["scenario_vessels"] == []
    assert client.get("/api/scene").json()["scenario_vessels"] == []
    payload = {"episode_id": episode, "command_id": "intent-1", "label": "sector", "bbox": [4, 4, 12, 12], "mode": "search_priority", "valid_duration_min": 20}
    assert client.post("/api/intents", json=payload).status_code == 200
    assert client.get("/api/intent-commands/intent-1").json()["status"] == "applied"


def test_chat_job_and_worker_completion(client):
    episode = client.get("/api/state").json()["episode_id"]
    response = client.post("/api/pi-agent/messages", json={"episode_id": episode, "text": "Observe only"})
    assert response.status_code == 200
    headers = {"Authorization": "Bearer test-worker"}
    job = client.post("/internal/agent/next", json={}, headers=headers).json()["job"]
    assert job["run_id"] == response.json()["run_id"]
    assert client.post("/internal/agent/event", json={"run_id": job["run_id"], "episode_id": episode, "type": "completed", "text": "Observed", "decision_reason": "当前观测不要求调整"}, headers=headers).status_code == 200
    assert client.get("/api/pi-agent/messages").json()["messages"][-1]["text"] == "Observed"
    assert any(event["type"] == "agent_decision_recorded" and event["data"]["decision_reason"] == "当前观测不要求调整" for event in client.app.state.runtime.events)


def test_completion_without_plan_requires_public_reason(client):
    runtime = client.app.state.runtime
    client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode, "text": "Observe"})
    headers = {"Authorization": "Bearer test-worker"}
    job = client.post("/internal/agent/next", json={}, headers=headers).json()["job"]
    data = {"run_id": job["run_id"], "episode_id": runtime.episode, "type": "completed", "text": "No change"}
    response = client.post("/internal/agent/event", json=data, headers=headers)
    assert response.status_code == 422
    assert response.json()["error_code"] == "decision_reason_required"
    assert job["run_id"] in [item["run_id"] for item in runtime.agent_jobs if item["status"] == "running"]


def test_inline_approval_decision_controls_future_worker_jobs(client):
    runtime = client.app.state.runtime
    runtime.set_mode("request")
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    pending = client.post("/api/algorithm/commands", json={"episode_id": runtime.episode,
        "result_id": candidate["result_id"], "command_id": "approval-test", "decision_reason": "覆盖北部空白"}).json()
    assert pending["status"] == "pending_approval"
    assert pending["decision_reason"] == "覆盖北部空白"
    client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode, "text": "Inspect"})
    headers = {"Authorization": "Bearer test-worker"}
    assert client.post("/internal/agent/next", json={}, headers=headers).json()["job"] is None
    assert client.post(f"/api/approvals/{pending['plan_id']}/decision", json={"episode_id": runtime.episode,
        "decision": "invalid"}).status_code == 422
    approved = client.post(f"/api/approvals/{pending['plan_id']}/decision", json={"episode_id": runtime.episode,
        "decision": "approve_session"}).json()
    assert approved["status"] == "active"
    assert approved["approval_scope"] == "approve_session"
    assert client.post("/internal/agent/next", json={}, headers=headers).json()["job"] is not None


def test_worker_submission_requires_public_decision_reason(client):
    runtime = client.app.state.runtime
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode, "text": "Review coverage"})
    headers = {"Authorization": "Bearer test-worker"}
    job = client.post("/internal/agent/next", json={}, headers=headers).json()["job"]
    payload = {"episode_id": runtime.episode, "run_id": job["run_id"], "result_id": candidate["result_id"], "command_id": "no-reason"}
    missing = client.post("/internal/tools/submit_mission_plan", json=payload, headers=headers)
    assert missing.status_code == 422
    assert missing.json()["error_code"] == "decision_reason_required"
    assert runtime.plans == {}
    accepted = client.post("/internal/tools/submit_mission_plan", json={**payload, "decision_reason": "覆盖北侧空白"}, headers=headers)
    assert accepted.status_code == 200
    assert accepted.json()["decision_reason"] == "覆盖北侧空白"
    assert any(event["type"] == "agent_plan_decided" and event["data"]["run_id"] == job["run_id"]
        and event["data"]["plan_id"] == accepted.json()["plan_id"] for event in runtime.events)


def test_debug_events_are_explicit(client):
    episode = client.get("/api/state").json()["episode_id"]
    assert client.post("/api/test/target-lost", json={"episode_id": episode}).status_code == 403
    assert client.post("/api/test/target-lost", json={"episode_id": episode, "debug": True}).status_code == 200


def test_approval_comment_is_recorded(client):
    runtime = client.app.state.runtime
    runtime.set_mode("request")
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    pending = client.post("/api/algorithm/commands", json={"episode_id": runtime.episode,
        "result_id": candidate["result_id"], "command_id": "commented", "decision_reason": "覆盖北部空白"}).json()
    assert pending["status"] == "pending_approval"
    assert client.post(f"/api/approvals/{pending['plan_id']}/decision", json={"episode_id": runtime.episode,
        "decision": "approve_once", "comment": " "}).status_code == 422
    assert client.post(f"/api/approvals/{pending['plan_id']}/decision", json={"episode_id": runtime.episode,
        "decision": "approve_once", "comment": "x"*501}).status_code == 422
    approved = client.post(f"/api/approvals/{pending['plan_id']}/decision", json={"episode_id": runtime.episode,
        "decision": "approve_once", "comment": "同意，注意航迹间距"}).json()
    assert approved["status"] == "active"
    assert approved["approval_comment"] == "同意，注意航迹间距"
    event = next(e for e in runtime.events if e["type"] == "approval_decided" and e["data"]["plan_id"] == pending["plan_id"])
    assert event["data"]["comment"] == "同意，注意航迹间距"


def test_fuel_shortage_engages_energy_exit_lifecycle(client):
    runtime = client.app.state.runtime
    episode = client.get("/api/state").json()["episode_id"]
    assert client.post("/api/test/fuel-shortage", json={"episode_id": episode}).status_code == 403
    assert client.post("/api/test/fuel-shortage", json={"episode_id": episode, "debug": True,
        "uuv_id": "NOPE"}).status_code == 404
    runtime.obstacles = []
    for index, boat in enumerate(runtime.uuvs):
        boat["pose"] = [400 if index < 4 else 3000, 400+(index % 4)*1000, 0]
    runtime.set_mode("full")
    fleet = runtime.calculate("plan_search", {"standing_policy": True})
    assert fleet["status"] == "succeeded"
    runtime.submit(fleet["result_id"], "fleet", runtime.episode)
    runtime.contacts["CONTACT-1"] = {"contact_id": "CONTACT-1", "x": 1100, "y": 1100, "vx": 0, "vy": 0,
        "uncertainty_m": 30, "state": "confirmed", "last_seen": 0, "samples": []}
    tracking = runtime.calculate("plan_tracking", {"members": ["UUV-1", "UUV-2"], "contact_id": "CONTACT-1"})
    assert tracking["status"] == "succeeded"
    runtime.submit(tracking["result_id"], "track", runtime.episode)
    runtime.uuvs[0]["pose"] = [2000, 2000, 0]
    response = client.post("/api/test/fuel-shortage", json={"episode_id": episode, "debug": True})
    assert response.status_code == 200, response.json()
    drained = response.json()
    boat = next(u for u in runtime.uuvs if u["id"] == drained["uuv_id"])
    x, y = boat["pose"][:2]
    edge = min(x, y, runtime.config.width-x, runtime.config.height-y)
    assert runtime.active[drained["uuv_id"]]["kind"] == "track"
    assert 0 < boat["remaining_range_m"] < 1.5*edge
    assert any(e["type"] == "debug_fuel_shortage" and e["data"]["uuv_id"] == drained["uuv_id"] for e in runtime.events)
    runtime.start()
    runtime.tick()
    assert runtime.active[drained["uuv_id"]]["kind"] == "exit"
    assert any(e["type"] == "energy_exit_started" and e["data"]["uuv_id"] == drained["uuv_id"] for e in runtime.events)


def test_invalid_video_rejected(client):
    assert client.post("/api/export/mp4", content=b"not video", headers={"Content-Type": "video/webm"}).status_code in (422, 503)


@pytest.mark.parametrize("tool,algorithm", [
    ("compute_task_allocation", "distance_band"),
    ("compute_task_allocation", "team_milp"),
    ("plan_path", "strip_coverage"),
    ("plan_search", "dubins_hybrid"),
    ("plan_tracking", "slot_assignment"),
])
def test_algorithm_id_must_match_the_requested_tool(client, tool, algorithm):
    episode = client.get("/api/state").json()["episode_id"]
    response = client.post("/api/algorithm/task-plan", json={
        "episode_id": episode, "tool": tool, "algorithm_id": algorithm, "members": ["UUV-1"],
    })
    assert response.status_code == 422
    assert response.json()["error_code"] == "unknown_algorithm"


def test_algorithm_registry_advertises_actual_slot_assignment(client):
    algorithms = client.get("/api/algorithm/status").json()["algorithms"]
    assert "slot_assignment" in algorithms
    assert "team_milp" not in algorithms
    episode = client.get("/api/state").json()["episode_id"]
    response = client.post("/api/algorithm/task-plan", json={
        "episode_id": episode, "tool": "compute_task_allocation", "algorithm_id": "slot_assignment",
    })
    assert response.status_code == 200
    assert response.json()["algorithm"] == "connected-workload-assignment-v2"
    teams = response.json()["teams"]
    assert len(teams) == 8
    assert all(len(team["members"]) == 1 for team in teams)
    assert {member for team in teams for member in team["members"]} == {f"UUV-{i}" for i in range(1, 9)}


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_nonfinite_json_is_rejected_before_mutation(client, value):
    episode = client.get("/api/state").json()["episode_id"]
    response = client.post("/api/simulation/start", content=f'{{"episode_id":"{episode}","extra":{value}}}',
        headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert client.app.state.runtime.status == "ready"


@pytest.mark.parametrize("members", [[["UUV-1"]], [{"id": "UUV-1"}], [1]])
def test_team_members_must_be_strings(client, members):
    episode = client.get("/api/state").json()["episode_id"]
    response = client.post("/api/algorithm/task-plan", json={
        "episode_id": episode, "tool": "plan_search", "members": members,
    })
    assert response.status_code == 422


def test_agent_intents_use_meters_while_ui_keeps_cell_coordinates(client):
    runtime = client.app.state.runtime
    client.post("/api/intents", json={"episode_id": runtime.episode, "command_id": "coordinate-intent", "bbox": [4, 5, 12, 20]}).raise_for_status()
    assert runtime.mission_state()["intents"][0]["bbox"] == [400, 2000, 1200, 3500]
    assert client.get("/api/state").json()["intents"][0]["bbox"] == [4, 5, 12, 20]
