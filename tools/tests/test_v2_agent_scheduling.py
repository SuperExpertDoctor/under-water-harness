import pytest
from fastapi.testclient import TestClient

from uuv_game.api import create_app


HEADERS = {"Authorization": "Bearer scheduling-worker"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/"schedule.sqlite", worker_token="scheduling-worker", ticking=False)) as client:
        yield client


def dispatch(client, **payload):
    response = client.post("/internal/agent/next", headers=HEADERS, json=payload)
    response.raise_for_status()
    return response.json()["job"]


def complete(client, job):
    client.post("/internal/agent/event", headers=HEADERS,
        json={"episode_id": job["episode_id"], "run_id": job["run_id"], "type": "completed"}).raise_for_status()


def test_material_wakeup_survives_queued_routine_and_latest_facts_are_durable(client):
    runtime = client.app.state.runtime
    routine = runtime.queue_agent("routine first", "periodic")
    urgent = runtime.queue_agent("CONTACT-1 confirmed; plan tracking", "target_found")
    assert urgent["run_id"] != routine["run_id"]
    assert urgent["source"] == "target_found"
    assert urgent["text"] == "CONTACT-1 confirmed; plan tracking"
    latest = runtime.queue_agent("CONTACT-1 lost; review reacquisition", "target_lost")
    assert latest["run_id"] == urgent["run_id"]
    assert latest["source"] == "target_lost"
    assert latest["text"] == "CONTACT-1 lost; review reacquisition"
    runtime.queue_agent("latest search completion", "search_complete")
    assert routine["source"] == "search_complete"
    assert routine["text"] == "latest search completion"
    saved = runtime.store.load()["agent_jobs"]
    assert saved == runtime.agent_jobs
    assert len(saved) == 2
    assert any(event["type"] == "agent_wakeup_coalesced" and event["data"]["run_id"] == urgent["run_id"] for event in runtime.events)


def test_human_and_feedback_precede_material_and_material_precedes_routine(client):
    runtime = client.app.state.runtime
    routine = runtime.queue_agent("routine", "periodic")
    urgent = runtime.queue_agent("confirmed", "target_found")
    human = runtime.queue_agent("human")
    feedback = runtime.queue_agent("recovered human feedback", "feedback")
    for expected in (human, feedback, urgent, routine):
        actual = dispatch(client)
        assert actual["run_id"] == expected["run_id"]
        complete(client, actual)


@pytest.mark.parametrize("source", ["target_found", "target_lost", "energy_exit", "human", "feedback"])
def test_routine_cooldown_does_not_block_material_or_human(client, source):
    runtime = client.app.state.runtime
    routine = runtime.queue_agent("routine", "periodic")
    assert dispatch(client, defer_routine=True) is None
    assert routine["status"] == "queued"
    urgent = runtime.queue_agent("urgent", source)
    assert dispatch(client, defer_routine=True)["run_id"] == urgent["run_id"]
    assert dispatch(client, defer_routine=False) is None, "Do not lease a second job while one is running"
    complete(client, urgent)
    assert dispatch(client, defer_routine=True) is None
    assert dispatch(client, defer_routine=False)["run_id"] == routine["run_id"]


@pytest.mark.parametrize("value", [None, "true", 1, 0, [], {}])
def test_defer_routine_requires_private_strict_boolean(client, value):
    runtime = client.app.state.runtime
    runtime.queue_agent("routine", "periodic")
    response = client.post("/internal/agent/next", headers=HEADERS, json={"defer_routine": value})
    assert response.status_code == 422
    assert runtime.agent_jobs[0]["status"] == "queued"
    assert client.post("/internal/agent/next", json={"defer_routine": True}).status_code == 403


def test_background_coalescing_remains_bounded_without_dropping_human_jobs(client):
    runtime = client.app.state.runtime
    humans = [runtime.queue_agent(f"human-{index}") for index in range(10)]
    for index in range(25):
        runtime.queue_agent(f"routine-{index}", "periodic")
        runtime.queue_agent(f"contact update-{index}", "target_found")
    assert len(runtime.agent_jobs) == 12
    assert all(job in runtime.agent_jobs for job in humans)
    assert {job["text"] for job in runtime.agent_jobs if job["source"] != "human"} == {"routine-24", "contact update-24"}
