import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from adapter.api import create_app


HEADERS = {"Authorization": "Bearer test-worker"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "mission.sqlite", worker_token="test-worker", ticking=False)) as client:
        client.get("/api/health")
        yield client


def start_job(client, text="Observe"):
    episode = client.app.state.runtime.episode
    client.post("/api/pi-agent/messages", json={"episode_id": episode, "text": text}).raise_for_status()
    job = client.post("/internal/agent/next", json={}, headers=HEADERS).json()["job"]
    return {"run_id": job["run_id"], "episode_id": episode}


def test_internal_tools_require_a_live_run_and_matching_episode(client):
    episode = client.app.state.runtime.episode
    assert client.post("/internal/tools/get_mission_state", json={"episode_id": episode}, headers=HEADERS).status_code == 409
    metadata = start_job(client)
    assert client.post("/internal/tools/get_mission_state", json=metadata, headers=HEADERS).status_code == 200
    assert client.post("/internal/tools/get_mission_state", json={**metadata, "episode_id": "old"}, headers=HEADERS).status_code == 409


def test_cancelled_run_cannot_submit_after_its_last_successful_heartbeat(client):
    metadata = start_job(client)
    runtime = client.app.state.runtime
    runtime.set_mode("full")
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    assert not client.post("/internal/agent/heartbeat", json=metadata, headers=HEADERS).json()["cancel"]
    client.post(f"/api/pi-agent/task-assignment/{metadata['run_id']}/cancel", json=metadata).raise_for_status()
    response = client.post("/internal/tools/submit_mission_plan", headers=HEADERS,
        json={**metadata, "result_id": candidate["result_id"], "command_id": "cancelled-submit"})
    assert response.status_code == 409
    assert not runtime.active


def test_expired_worker_lease_fails_abandoned_job_and_dispatches_next(client):
    metadata = start_job(client)
    runtime = client.app.state.runtime
    runtime.agent_jobs[0]["lease_deadline"] = time.monotonic() - 1
    client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode, "text": "Second"}).raise_for_status()
    next_job = client.post("/internal/agent/next", json={}, headers=HEADERS).json()["job"]
    assert next_job is not None
    assert next_job["run_id"] != metadata["run_id"]
    assert runtime.agent_jobs[0]["status"] == "failed"
    assert runtime.agent_jobs[0]["error"] == "worker_lease_expired"


def test_heartbeat_cannot_revive_expired_or_wrong_episode_job(client):
    metadata = start_job(client)
    runtime = client.app.state.runtime
    assert client.post("/internal/agent/heartbeat", json={**metadata, "episode_id": "old"}, headers=HEADERS).json()["cancel"]
    runtime.agent_jobs[0]["lease_deadline"] = time.monotonic() - 1
    assert client.post("/internal/agent/heartbeat", json=metadata, headers=HEADERS).json()["cancel"]
    assert runtime.agent_jobs[0]["status"] == "failed"


@pytest.mark.parametrize("ending", ["lease", "failed", "completed"])
def test_unacknowledged_feedback_survives_job_end_without_duplication(client, ending):
    metadata = start_job(client)
    runtime = client.app.state.runtime
    for text in ("keep first instruction", "keep second instruction"):
        client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode, "text": text}).raise_for_status()
    if ending == "lease":
        runtime.agent_jobs[0]["lease_deadline"] = time.monotonic()-1
    else:
        client.post("/internal/agent/event", headers=HEADERS, json={**metadata, "type": ending}).raise_for_status()
    client.post("/internal/agent/next", json={}, headers=HEADERS).raise_for_status()
    recovered = [job["text"] for job in runtime.agent_jobs if job["source"] == "feedback"]
    assert recovered == ["keep first instruction", "keep second instruction"]
    assert runtime.agent_jobs[0]["feedback"] == []
    client.post("/internal/agent/next", json={}, headers=HEADERS).raise_for_status()
    assert [job["text"] for job in runtime.agent_jobs if job["source"] == "feedback"] == recovered


def test_feedback_recovery_keeps_overflow_until_queue_capacity_returns(client):
    metadata = start_job(client)
    runtime = client.app.state.runtime
    for text in ("first feedback", "second feedback"):
        client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode, "text": text}).raise_for_status()
    for index in range(11):
        runtime.queue_agent(f"backlog-{index}", "feedback")
    client.post("/internal/agent/event", headers=HEADERS, json={**metadata, "type": "failed"}).raise_for_status()
    assert [item["text"] for item in runtime.agent_jobs[0]["feedback"]] == ["second feedback"]
    next_job = client.post("/internal/agent/next", json={}, headers=HEADERS).json()["job"]
    client.post("/internal/agent/event", headers=HEADERS, json={"episode_id": runtime.episode,
        "run_id": next_job["run_id"], "type": "completed"}).raise_for_status()
    client.post("/internal/agent/next", json={}, headers=HEADERS).raise_for_status()
    assert runtime.agent_jobs[0]["feedback"] == []
    recovered = [job["text"] for job in runtime.agent_jobs if job["text"] in ("first feedback", "second feedback")]
    assert recovered == ["first feedback", "second feedback"]


def test_reset_rejects_old_run_even_when_it_uses_new_episode(client):
    metadata = start_job(client)
    client.post("/api/simulation/reset", json=metadata).raise_for_status()
    metadata["episode_id"] = client.app.state.runtime.episode
    assert client.post("/internal/tools/get_mission_state", json=metadata, headers=HEADERS).status_code == 409


def test_cancel_remains_responsive_while_calculation_is_running(client, monkeypatch):
    metadata = start_job(client)
    runtime = client.app.state.runtime
    original = runtime.calculate
    entered, released = threading.Event(), threading.Event()

    def blocked_calculation(name, data):
        entered.set()
        assert released.wait(3)
        return original(name, data)

    monkeypatch.setattr(runtime, "calculate", blocked_calculation)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(client.post, "/internal/tools/compute_task_allocation", json=metadata, headers=HEADERS)
        try:
            assert entered.wait(3)
            client.post(f"/api/pi-agent/task-assignment/{metadata['run_id']}/cancel", json=metadata).raise_for_status()
        finally:
            released.set()
        assert pending.result(timeout=3).status_code == 409
    assert not runtime.active


def test_state_websocket_emits_reset_when_new_cursor_matches_old(client):
    runtime = client.app.state.runtime
    runtime.start()
    old_episode = runtime.episode
    endpoint = next(route.endpoint for route in client.app.routes if route.path == "/ws/{stream}")

    class Socket:
        headers = {}

        def __init__(self):
            self.messages = []
            self.received = 0

        async def accept(self):
            pass

        async def send_json(self, message):
            self.messages.append(message)

        async def receive_text(self):
            self.received += 1
            if self.received == 1:
                runtime.reset()
                return "next"
            raise WebSocketDisconnect()

    socket = Socket()
    asyncio.run(endpoint(socket, "state"))
    assert len(socket.messages) == 2
    assert socket.messages[0]["episode_id"] == old_episode
    assert socket.messages[1]["episode_id"] == runtime.episode
    assert socket.messages[1]["events"][0]["type"] == "environment_reset"


def test_skill_endpoint_returns_the_real_trusted_skill(client):
    path = Path(__file__).resolve().parents[2] / "agent/skills/multi-uuv-recon-tracking/SKILL.md"
    assert client.get("/api/skills/multi-uuv-recon-tracking").json()["content"] == path.read_text()


def test_intent_receipts_and_task_mutations_hold_the_runtime_lock(client, monkeypatch):
    runtime = client.app.state.runtime
    original_receipts = runtime.receipts

    class LockedReceipts:
        def get(self, key):
            assert runtime.lock._is_owned()
            return original_receipts.get(key)

        def __setitem__(self, key, value):
            assert runtime.lock._is_owned()
            original_receipts[key] = value

    class LockedTasks(list):
        def append(self, value):
            assert runtime.lock._is_owned()
            super().append(value)

    monkeypatch.setattr(runtime, "receipts", LockedReceipts())
    monkeypatch.setattr(runtime, "tasks", LockedTasks())
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    episode = runtime.episode
    client.post("/api/intents", json={"episode_id": episode, "command_id": "intent-locked", "bbox": [4, 4, 12, 12]}).raise_for_status()
    client.post("/api/task-assembly/assemble", json={"episode_id": episode, "result_id": candidate["result_id"]}).raise_for_status()


def test_submit_rolls_back_receipt_checkpoint_and_memory_when_save_fails(client, monkeypatch):
    runtime = client.app.state.runtime
    runtime.set_mode("full")
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    before = runtime.store.load()
    original = runtime.store.save

    def fail_after_save(data):
        original(data)
        raise OSError("injected_save_failure")

    with monkeypatch.context() as patch:
        patch.setattr(runtime.store, "save", fail_after_save)
        with pytest.raises(OSError, match="injected_save_failure"):
            runtime.submit(candidate["result_id"], "atomic-submit", runtime.episode)
    assert runtime.store.get_receipt("atomic-submit") is None
    assert runtime.store.load() == before
    assert not runtime.active
    assert not runtime.plans
    assert runtime.revision == before["revision"]


def test_intent_receipt_rolls_back_nested_saves_and_memory(client, monkeypatch):
    runtime = client.app.state.runtime
    runtime.save()
    before = runtime.store.load()
    original = runtime.store.save
    saves = 0

    def fail_second_save(data):
        nonlocal saves
        saves += 1
        original(data)
        if saves == 2:
            raise OSError("injected_second_save_failure")

    with monkeypatch.context() as patch:
        patch.setattr(runtime.store, "save", fail_second_save)
        with pytest.raises(OSError, match="injected_second_save_failure"):
            client.post("/api/intents", json={"episode_id": runtime.episode, "command_id": "atomic-intent", "bbox": [4, 4, 12, 12]})
    assert runtime.store.get_receipt("atomic-intent") is None
    assert runtime.store.load() == before
    assert not runtime.intents
    assert not runtime.agent_jobs
    assert runtime.cursor == before["cursor"]


def test_nested_store_transactions_rollback_only_the_failed_scope(client):
    runtime = client.app.state.runtime
    with runtime.lock, runtime.store.transaction():
        runtime.receipts["outer"] = {"response": {"status": "applied"}}
        with pytest.raises(ValueError):
            with runtime.store.transaction():
                runtime.receipts["inner"] = {"response": {"status": "applied"}}
                raise ValueError("abort inner scope")
        assert runtime.store.get_receipt("inner") is None
    assert runtime.store.get_receipt("outer") is not None


def test_archived_plan_remains_queryable(client):
    runtime = client.app.state.runtime
    runtime.store.archive_plan({"plan_id": "archived-plan", "status": "superseded"})
    response = client.get("/api/plans/archived-plan")
    assert response.status_code == 200
    assert response.json()["status"] == "superseded"
    assert client.get("/api/actions/archived-plan").json()["status"] == "superseded"


def test_tick_failure_is_explicit_and_loop_survives(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(tmp_path / "mission.sqlite", worker_token="test-worker")
        runtime = app.state.runtime
        original = runtime.tick
        failed = False

        def fail_once():
            nonlocal failed
            if not failed:
                failed = True
                raise RuntimeError("unexpected_tick_failure")
            original()

        monkeypatch.setattr(runtime, "tick", fail_once)
        async with app.router.lifespan_context(app):
            runtime.start()
            deadline = time.monotonic() + 2
            while not failed and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            assert failed
            assert runtime.status == "safety_paused"
            assert any(event["type"] == "runtime_error" for event in runtime.events)
            runtime.start()
            deadline = time.monotonic() + 2
            while runtime.sim_time == 0 and time.monotonic() < deadline:
                await asyncio.sleep(.01)
            assert runtime.sim_time > 0

    asyncio.run(scenario())
