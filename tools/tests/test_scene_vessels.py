import math
import json

import pytest
from fastapi.testclient import TestClient

from uuv_game.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "mission.sqlite", worker_token="test-worker", ticking=False)) as client:
        client.get("/api/health")
        yield client


def add_vessel(client, vessel_class="type_ii", position=None):
    runtime = client.app.state.runtime
    response = client.post("/api/vessels", json={"episode_id": runtime.episode,
        "command_id": f"add-{len(runtime.vessels)}", "vessel_class": vessel_class, "position_cells": position or [25, 10]})
    response.raise_for_status()
    return client.get("/api/state").json()["scenario_vessels"][-1]


def toggle_ais(client, vessel, enabled):
    return client.patch(f"/api/vessels/{vessel['scenario_entity_id']}/ais", json={
        "episode_id": client.app.state.runtime.episode, "command_id": f"ais-{vessel['scenario_entity_id']}-{enabled}",
        "expected_revision": vessel["revision"], "ais_enabled": enabled,
    })


def test_scene_vessel_is_a_moving_si_target_with_stable_scene_revision(client):
    vessel = add_vessel(client)
    runtime = client.app.state.runtime
    target = next((target for target in runtime.targets if target["id"] == vessel["scenario_entity_id"]), None)
    assert target is not None
    assert target["pose"][:2] == [2550, 2950]
    assert target["pose"][2] == pytest.approx(math.atan2(-950, -550))
    before = target["pose"][:]
    runtime.start()
    for _ in range(10):
        runtime.tick()
    assert 0 < math.dist(before[:2], target["pose"][:2]) <= 5.01
    scene = runtime.frame()["scenario_vessels"][0]
    assert scene["position"] == pytest.approx(runtime.cells(target["pose"]))
    assert scene["revision"] == vessel["revision"]
    assert "heading_deg" in scene


def test_ais_is_observed_without_uuv_sensor_and_hidden_target_stays_hidden(client):
    vessel = add_vessel(client)
    runtime = client.app.state.runtime
    assert runtime.mission_state()["contacts"] == []
    assert "targets" not in runtime.mission_state()
    assert "scenario_vessels" not in runtime.mission_state()
    runtime.sensor_enabled = False
    runtime.start()
    for _ in range(15):
        runtime.tick()
    contacts = runtime.mission_state()["contacts"]
    assert [contact["contact_id"] for contact in contacts] == [vessel["scenario_entity_id"]]
    assert contacts[0]["state"] == "confirmed"
    assert contacts[0]["vessel_class"] == "type_ii"
    assert contacts[0]["ais_mmsi"]
    assert contacts[0]["samples"][-1]["source"] == "ais"
    assert contacts[0]["samples"][-1]["observers"] == []
    frame_contact = runtime.frame()["contacts"][0]
    assert frame_contact["vessel_class"] == "type_ii"
    assert frame_contact["samples"][-1]["position"]


def test_disabled_ais_does_not_reveal_unseen_vessel_and_existing_contact_is_lost(client):
    vessel = add_vessel(client)
    runtime = client.app.state.runtime
    toggle_ais(client, vessel, False).raise_for_status()
    target = next((target for target in runtime.targets if target["id"] == vessel["scenario_entity_id"]), None)
    assert target is not None and target["ais_enabled"] is False
    runtime.start()
    for _ in range(15):
        runtime.tick()
    assert runtime.contacts == {}
    vessel = runtime.frame()["scenario_vessels"][0]
    toggle_ais(client, vessel, True).raise_for_status()
    for _ in range(15):
        runtime.tick()
    contact = runtime.contacts[vessel["scenario_entity_id"]]
    assert contact["state"] == "confirmed"
    last_seen = contact["last_seen"]
    vessel = runtime.frame()["scenario_vessels"][0]
    client.patch(f"/api/vessels/{vessel['scenario_entity_id']}/ais", json={"episode_id": runtime.episode,
        "command_id": "disable-again", "expected_revision": vessel["revision"], "ais_enabled": False}).raise_for_status()
    for _ in range(80):
        runtime.tick()
    assert contact["state"] == "lost"
    assert contact["last_seen"] == last_seen


def test_ais_off_can_be_seen_by_an_actual_nearby_active_sensor(client):
    vessel = add_vessel(client, position=[5, 35])
    toggle_ais(client, vessel, False).raise_for_status()
    runtime = client.app.state.runtime
    runtime.set_mode("full")
    result = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    runtime.submit(result["result_id"], "sensor-search", runtime.episode)
    runtime.start()
    for _ in range(5):
        runtime.tick()
    contact = runtime.contacts.get(vessel["scenario_entity_id"])
    assert contact is not None
    assert contact["samples"][-1]["source"] == "sensor"
    assert contact["samples"][-1]["observers"] == ["UUV-1"]
    assert "ais_mmsi" not in contact


def test_delete_removes_physics_contact_and_observations(client):
    vessel = add_vessel(client)
    runtime = client.app.state.runtime
    runtime.start()
    for _ in range(15):
        runtime.tick()
    entity_id = vessel["scenario_entity_id"]
    assert entity_id in runtime.contacts
    runtime.set_mode("full")
    candidate = runtime.calculate("plan_tracking", {"members": ["UUV-1"], "contact_id": entity_id})
    runtime.submit(candidate["result_id"], "track-scene-vessel", runtime.episode)
    client.request("DELETE", f"/api/vessels/{entity_id}", json={"episode_id": runtime.episode,
        "command_id": "delete-scene", "expected_revision": vessel["revision"]}).raise_for_status()
    assert entity_id not in runtime.contacts
    assert all(target["id"] != entity_id for target in runtime.targets)
    assert all(sample["contact_id"] != entity_id for sample in runtime.observations)
    assert not runtime.frame()["scenario_vessels"]
    runtime.tick()
    assert runtime.status == "safety_paused"
    assert runtime.events[-1]["type"] == "safety_tracking_contact_lost"


def test_type_i_ais_is_fixed_and_scene_capacity_is_twenty(client):
    first = add_vessel(client, "type_i")
    assert toggle_ais(client, first, False).status_code == 409
    for _ in range(19):
        add_vessel(client)
    runtime = client.app.state.runtime
    assert len(runtime.targets) == 21
    response = client.post("/api/vessels", json={"episode_id": runtime.episode,
        "command_id": "over-capacity", "vessel_class": "type_ii", "position_cells": [20, 20]})
    assert response.status_code == 409
    assert len(runtime.targets) == 21


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
