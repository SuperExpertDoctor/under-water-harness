"""Worker cooldown liveness must not extend active-run authorization leases."""

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from uuv_game import api


@pytest.mark.parametrize("heartbeat_age, expected", [(16, "idle"), (31, "offline")])
def test_lifespan_distinguishes_worker_cooldown_from_disconnect(tmp_path, heartbeat_age, expected):
    app = api.create_app(tmp_path / "liveness.sqlite", worker_token="test-worker")
    runtime = app.state.runtime
    runtime.last_heartbeat = api.time.monotonic()-heartbeat_age
    runtime.agent["status"] = "idle"

    async def observe_lifespan():
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0)
            assert runtime.agent["status"] == expected

    asyncio.run(observe_lifespan())


@pytest.mark.parametrize("renew", [False, True])
def test_worker_liveness_grace_does_not_extend_fifteen_second_tool_lease(tmp_path, monkeypatch, renew):
    app = api.create_app(tmp_path / "lease.sqlite", worker_token="test-worker", ticking=False)
    clock = [1000.0]
    monkeypatch.setattr(api, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    headers = {"Authorization": "Bearer test-worker"}
    with TestClient(app) as client:
        client.get("/api/health").raise_for_status()
        runtime = app.state.runtime
        client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode, "text": "Observe"}).raise_for_status()
        response = client.post("/internal/agent/next", json={}, headers=headers)
        response.raise_for_status()
        job = response.json()["job"]
        assert job["lease_deadline"] == clock[0]+15
        metadata = {"episode_id": runtime.episode, "run_id": job["run_id"]}
        if renew:
            clock[0] += 6
            client.post("/internal/agent/heartbeat", json=metadata, headers=headers).raise_for_status()
        deadline = clock[0]+15
        assert runtime.agent_jobs[0]["lease_deadline"] == deadline
        clock[0] = deadline-.01
        client.post("/internal/tools/get_mission_state", json=metadata, headers=headers).raise_for_status()
        clock[0] = deadline
        response = client.post("/internal/tools/get_mission_state", json=metadata, headers=headers)
        assert response.status_code == 409
        assert response.json()["error_code"] == "run_not_active"
        assert runtime.agent_jobs[0]["status"] == "failed"
        assert runtime.agent_jobs[0]["error"] == "worker_lease_expired"
