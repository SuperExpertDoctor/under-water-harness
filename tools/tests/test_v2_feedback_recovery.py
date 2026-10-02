"""Feedback recovery preserves operator chronology and original message identity."""

from fastapi.testclient import TestClient
import pytest

from adapter.api import create_app


HEADERS = {"Authorization": "Bearer test-worker"}


@pytest.fixture
def feedback_run(tmp_path):
    with TestClient(create_app(tmp_path / "feedback-recovery.sqlite", worker_token="test-worker", ticking=False)) as client:
        client.get("/api/health").raise_for_status()
        runtime = client.app.state.runtime
        client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode, "text": "Observe"}).raise_for_status()
        response = client.post("/internal/agent/next", json={}, headers=HEADERS)
        response.raise_for_status()
        metadata = {"episode_id": runtime.episode, "run_id": response.json()["job"]["run_id"]}
        client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode,
            "text": "Earlier: track the contact", "delivery": "steer"}).raise_for_status()
        earlier = client.get("/api/pi-agent/messages").json()["messages"][-1]
        client.post("/internal/agent/heartbeat", headers=HEADERS,
            json={**metadata, "acknowledged_feedback_ids": [earlier["feedback_id"]]}).raise_for_status()
        client.post("/api/pi-agent/messages", json={"episode_id": runtime.episode,
            "text": "Later: do not track the contact", "delivery": "followUp"}).raise_for_status()
        later = client.get("/api/pi-agent/messages").json()["messages"][-1]
        yield client, metadata, earlier, later


@pytest.mark.parametrize("ending", ["failed", "lease_expired"])
def test_recovered_accepted_feedback_precedes_newer_pending_feedback(feedback_run, ending):
    client, metadata, earlier, later = feedback_run
    runtime = client.app.state.runtime
    if ending == "failed":
        client.post("/internal/agent/event", json={**metadata, "type": "failed"}, headers=HEADERS).raise_for_status()
    else:
        runtime.agent_jobs[0]["lease_deadline"] = 0
    response = client.post("/internal/agent/next", json={}, headers=HEADERS)
    response.raise_for_status()
    recovered = [job for job in runtime.agent_jobs if job["source"] == "feedback"]
    assert [job["text"] for job in recovered] == [earlier["text"], later["text"]]
    assert response.json()["job"]["text"] == earlier["text"]
    assert not runtime.agent_jobs[0].get("feedback")
    assert not runtime.agent_jobs[0].get("accepted_feedback")


@pytest.mark.parametrize("ending", ["failed", "lease_expired"])
def test_recovered_runs_finish_original_user_messages_without_duplicates(feedback_run, ending):
    client, metadata, earlier, later = feedback_run
    runtime = client.app.state.runtime
    original_users = [message for message in client.get("/api/pi-agent/messages").json()["messages"] if message["role"] == "user"]
    original_ids = [message["id"] for message in original_users]
    if ending == "failed":
        client.post("/internal/agent/event", json={**metadata, "type": "failed"}, headers=HEADERS).raise_for_status()
    else:
        runtime.agent_jobs[0]["lease_deadline"] = 0
    for original in (earlier, later):
        response = client.post("/internal/agent/next", json={}, headers=HEADERS)
        response.raise_for_status()
        recovered = response.json()["job"]
        assert recovered is not None
        assert recovered["source"] == "feedback"
        assert recovered.get("source_feedback_id") == original["feedback_id"]
        assert recovered["run_id"] != metadata["run_id"]
        users = [message for message in client.get("/api/pi-agent/messages").json()["messages"] if message["role"] == "user"]
        current = next(message for message in users if message["id"] == original["id"])
        assert current["run_id"] == recovered["run_id"]
        assert current["feedback_id"] == original["feedback_id"]
        assert current["status"] == "queued"
        assert [message["id"] for message in users] == original_ids
        client.post("/internal/agent/event", headers=HEADERS, json={"episode_id": runtime.episode,
            "run_id": recovered["run_id"], "type": "completed", "text": "Feedback processed"}).raise_for_status()
        users = [message for message in client.get("/api/pi-agent/messages").json()["messages"] if message["role"] == "user"]
        completed = next(message for message in users if message["id"] == original["id"])
        assert completed["status"] == "completed"
        assert completed["delivery_status"] == "processed"
        assert completed["run_id"] == recovered["run_id"]
        assert [message["id"] for message in users] == original_ids


@pytest.mark.parametrize("ending", ["failed", "lease_expired"])
def test_recovered_feedback_is_not_stranded_by_another_worker_failure(feedback_run, ending):
    client, metadata, earlier, _later = feedback_run
    runtime = client.app.state.runtime
    client.post("/internal/agent/event", json={**metadata, "type": "failed"}, headers=HEADERS).raise_for_status()
    response = client.post("/internal/agent/next", json={}, headers=HEADERS)
    response.raise_for_status()
    recovered = response.json()["job"]
    assert recovered["source_feedback_id"] == earlier["feedback_id"]
    if ending == "failed":
        client.post("/internal/agent/event", headers=HEADERS, json={"episode_id": runtime.episode,
            "run_id": recovered["run_id"], "type": "failed"}).raise_for_status()
    else:
        next(job for job in runtime.agent_jobs if job["run_id"] == recovered["run_id"])["lease_deadline"] = 0
    response = client.post("/internal/agent/next", json={}, headers=HEADERS)
    response.raise_for_status()
    assert response.json()["job"]["source_feedback_id"] == earlier["feedback_id"]
    users = client.get("/api/pi-agent/messages").json()["messages"]
    original = next(message for message in users if message["id"] == earlier["id"])
    active_retry = next((job for job in runtime.agent_jobs if job.get("source_feedback_id") == earlier["feedback_id"]
        and job["status"] in ("queued", "running")), None)
    assert active_retry is not None
    assert active_retry["run_id"] != recovered["run_id"]
    assert original["run_id"] == active_retry["run_id"]
    assert original["status"] == "queued"


def test_feedback_recovery_exhaustion_is_visible_and_does_not_block_newer_input(feedback_run):
    client, metadata, earlier, later = feedback_run
    runtime = client.app.state.runtime
    client.post("/internal/agent/event", json={**metadata, "type": "failed"}, headers=HEADERS).raise_for_status()
    for _attempt in range(3):
        response = client.post("/internal/agent/next", json={}, headers=HEADERS)
        response.raise_for_status()
        job = response.json()["job"]
        assert job["source_feedback_id"] == earlier["feedback_id"]
        client.post("/internal/agent/event", headers=HEADERS, json={"episode_id": runtime.episode,
            "run_id": job["run_id"], "type": "failed"}).raise_for_status()
    response = client.post("/internal/agent/next", json={}, headers=HEADERS)
    response.raise_for_status()
    assert response.json()["job"]["source_feedback_id"] == later["feedback_id"]
    original = next(message for message in runtime.messages if message["id"] == earlier["id"])
    assert original["status"] == "failed"
    assert original["delivery_status"] == "failed"
    assert not any(job.get("source_feedback_id") == earlier["feedback_id"] and job["status"] in ("queued", "running")
        for job in runtime.agent_jobs)
    assert len([message for message in runtime.messages if message["id"] == earlier["id"]]) == 1


def test_cancelled_recovery_does_not_retry(feedback_run):
    client, metadata, earlier, later = feedback_run
    runtime = client.app.state.runtime
    client.post("/internal/agent/event", json={**metadata, "type": "failed"}, headers=HEADERS).raise_for_status()
    recovered = client.post("/internal/agent/next", json={}, headers=HEADERS).json()["job"]
    client.post(f"/api/pi-agent/task-assignment/{recovered['run_id']}/cancel",
        json={"episode_id": runtime.episode}).raise_for_status()
    response = client.post("/internal/agent/next", json={}, headers=HEADERS)
    response.raise_for_status()
    assert response.json()["job"]["source_feedback_id"] == later["feedback_id"]
    assert next(message for message in runtime.messages if message["id"] == earlier["id"])["status"] == "cancelled"


def test_retry_keeps_order_when_source_job_is_evicted_at_history_limit(feedback_run):
    client, metadata, earlier, later = feedback_run
    runtime = client.app.state.runtime
    client.post("/internal/agent/event", json={**metadata, "type": "failed"}, headers=HEADERS).raise_for_status()
    recovered = client.post("/internal/agent/next", json={}, headers=HEADERS).json()["job"]
    first = next(job for job in runtime.agent_jobs if job["run_id"] == recovered["run_id"])
    second = next(job for job in runtime.agent_jobs if job.get("source_feedback_id") == later["feedback_id"])
    runtime.agent_jobs = [first, *[{"run_id": f"finished-{index}", "episode_id": runtime.episode,
        "status": "completed", "source": "periodic", "text": "Done", "feedback": []} for index in range(98)], second]
    client.post("/internal/agent/event", headers=HEADERS, json={"episode_id": runtime.episode,
        "run_id": recovered["run_id"], "type": "failed"}).raise_for_status()
    response = client.post("/internal/agent/next", json={}, headers=HEADERS)
    response.raise_for_status()
    assert response.json()["job"]["source_feedback_id"] == earlier["feedback_id"]
    assert len(runtime.agent_jobs) == 100
