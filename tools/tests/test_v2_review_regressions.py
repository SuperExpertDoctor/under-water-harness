"""Offline regressions for review findings, not long-duration acceptance evidence."""

import copy
import json
import math
import zlib

import pytest

from uuv_game.capabilities.lifecycle import replacement_pose
from uuv_game.runtime import MissionError, MissionRuntime
from uuv_game.store import Store


@pytest.fixture
def runtime(tmp_path):
    runtime = MissionRuntime(tmp_path / "review.sqlite")
    runtime.obstacles = []
    yield runtime
    runtime.close()


def test_nearby_exits_cannot_reassign_another_boundary_entry(runtime):
    for index, other in enumerate(runtime.uuvs[2:]):
        other["pose"] = [1800+index*200, 1000, 0]
    for boat, y in zip(runtime.uuvs[:2], (2100, 2160)):
        boat["pose"] = [.5, y, math.pi]
    next_poses = {boat["id"]: boat["pose"] for boat in runtime.uuvs}
    next_poses.update({"UUV-1": [-.5, 2100, math.pi], "UUV-2": [400, 2160, math.pi]})
    first = replacement_pose(runtime, runtime.uuvs[0], next_poses)
    assert first == [0, 2100, 0]
    second = replacement_pose(runtime, runtime.uuvs[1], {**next_poses, "UUV-1": first, "UUV-2": [-.5, 2160, math.pi]})
    assert second is None


def test_simultaneous_boundary_crossings_commit_separate_entries(runtime):
    # Inject two already-authorized exits just inside the boundary; tick performs
    # the crossing and replacement without mocking motion or lifecycle code.
    runtime.standing_policy = {"enabled": True, "energy_rotation": True,
        "local_repair": True, "lost_reacquire": True, "plan_id": "standing-exit"}
    runtime.scan_times = [[0.0]*40 for _ in range(40)]
    for index, other in enumerate(runtime.uuvs[2:]):
        other["pose"] = [1800+index*200, 1000, 0]
    for boat, y in zip(runtime.uuvs[:2], (2100, 2500)):
        boat["pose"] = [.1, y, math.pi]
        runtime.active[boat["id"]] = {"plan_id": "standing-exit", "kind": "exit",
            "phase": "exiting", "generation": 1, "index": 0, "slot": 0,
            "points": [[.1, y, math.pi], [-30, y, math.pi]],
            "execution_domain": [-100, -100, 4100, 4100]}
    runtime.start()
    runtime.tick()
    assert runtime.status == "running", runtime.events[-5:]
    assert len(runtime.uuvs) == 8
    assert [boat["generation"] for boat in runtime.uuvs[:2]] == [2, 2]
    assert runtime.metrics["rotation_count"] == 2
    assert len(runtime.regions) == 2
    for index, boat in enumerate(runtime.uuvs):
        for other in runtime.uuvs[index+1:]:
            assert math.dist(boat["pose"][:2], other["pose"][:2]) >= runtime.config.separation
    assert math.dist(runtime.uuvs[0]["pose"][:2], runtime.uuvs[1]["pose"][:2]) >= 150
    for boat, expected in zip(runtime.uuvs[:2], ([0, 2100], [0, 2500])):
        assert boat["pose"][:2] == pytest.approx(expected)


@pytest.fixture
def pending_region_plan(runtime):
    runtime.set_mode("full")
    for member, bbox in (("UUV-1", [300, 300, 1700, 1700]),
                         ("UUV-5", [2500, 300, 3900, 1700])):
        candidate = runtime.calculate("plan_search", {"members": [member], "bbox": bbox})
        assert candidate["status"] == "succeeded", candidate
        runtime.submit(candidate["result_id"], f"initial-{member}", runtime.episode)
    runtime.set_mode("request")
    candidate = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
    assert runtime.evaluate(candidate["result_id"])["valid"]
    pending = runtime.submit(candidate["result_id"], "pending-region-update", runtime.episode)
    assert pending["status"] == "pending_approval"
    return candidate, pending


def test_affected_region_change_invalidates_candidate_and_pending_approval(runtime, pending_region_plan):
    candidate, pending = pending_region_plan
    assignment = copy.deepcopy(runtime.active["UUV-1"])
    region = next(region for region in runtime.regions if region["owner"] == "UUV-1")
    region["cells"].pop()
    runtime.region_revision += 1
    runtime.revision += 1
    assessment = runtime.evaluate(candidate["result_id"])
    assert not assessment["valid"], "A changed responsibility must invalidate the old candidate even when plan ID and generation are unchanged"
    with pytest.raises(MissionError):
        runtime.decide(pending["plan_id"], True)
    assert runtime.plans[pending["plan_id"]]["status"] == "expired"
    assert runtime.active["UUV-1"] == assignment


def test_unaffected_region_change_does_not_expire_pending_approval(runtime, pending_region_plan):
    candidate, pending = pending_region_plan
    other = next(region for region in runtime.regions if region["owner"] == "UUV-5")
    other["cells"].pop()
    runtime.region_revision += 1
    runtime.revision += 1
    unchanged = copy.deepcopy(other)
    assert runtime.evaluate(candidate["result_id"])["valid"]
    assert runtime.decide(pending["plan_id"], True)["status"] == "active"
    assert next(region for region in runtime.regions if region["owner"] == "UUV-5") == unchanged


def test_replay_preserves_historical_message_content_and_episode(runtime):
    runtime.messages = [{"id": "native-message", "role": "assistant", "text": "Earlier text",
        "run_id": "run-original", "status": "streaming", "time": 0}]
    original_episode = runtime.episode
    runtime.store.frame(original_episode, runtime.frame())
    runtime.messages[0].update(text="Later text", status="completed")
    runtime.frame_id += 1
    runtime.store.frame(original_episode, runtime.frame())
    runtime.reset()
    runtime.messages = [{"id": "new-message", "role": "user", "text": "Different episode"}]
    replay = runtime.store.replay(original_episode, 0, 10)
    assert replay["total"] == 2
    assert [frame["messages"][0]["text"] for frame in replay["frames"]] == ["Earlier text", "Later text"]
    assert [frame["messages"][0]["status"] for frame in replay["frames"]] == ["streaming", "completed"]
    assert all(frame["episode_id"] == original_episode for frame in replay["frames"])
    assert "Different episode" not in json.dumps(replay)


def test_frame_conversation_is_bounded_and_detached_from_live_messages(runtime):
    runtime.messages = [{"id": f"message-{index}", "role": "assistant", "text": f"Message {index}"}
        for index in range(35)]
    frame = runtime.frame()
    assert [message["id"] for message in frame["messages"]] == [f"message-{index}" for index in range(5, 35)]
    runtime.messages[-1]["text"] = "Changed after snapshot"
    assert frame["messages"][-1]["text"] == "Message 34"


def test_store_reads_mixed_legacy_text_and_compressed_frames_after_reopen(tmp_path):
    path = tmp_path / "mixed-replay.sqlite"
    legacy = {"episode_id": "replay-episode", "frame_id": 1, "messages": []}
    current = {"episode_id": "replay-episode", "frame_id": 2,
        "messages": [{"id": "m", "role": "assistant", "text": "Repeated observation. "*500}]}
    store = Store(path)
    try:
        with store.transaction():
            store.db.execute("INSERT INTO frames(episode,data) VALUES(?,?)", ("replay-episode", json.dumps(legacy)))
        store.frame("replay-episode", current)
        payload = store.db.execute("SELECT data FROM frames ORDER BY id DESC LIMIT 1").fetchone()[0]
        assert isinstance(payload, bytes)
        assert json.loads(zlib.decompress(payload)) == current
        assert len(payload) < len(json.dumps(current).encode())/2
    finally:
        store.close()
    restored = Store(path)
    try:
        assert restored.replay("replay-episode", 0, 10) == {"total": 2, "frames": [legacy, current]}
        assert restored.replay("replay-episode", 1, 1) == {"total": 2, "frames": [current]}
    finally:
        restored.close()
