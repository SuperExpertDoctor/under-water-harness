"""Deterministic game scenarios, deliberately independent of a paid model."""
import math

from uuv_game.runtime import MissionRuntime


def deploy_fleet(runtime):
    sectors = [(["UUV-1", "UUV-2", "UUV-3"], [600, 250, 3750, 1350]),
               (["UUV-4", "UUV-5", "UUV-6"], [600, 1450, 3750, 2550]),
               (["UUV-7", "UUV-8"], [600, 2650, 3750, 3750])]
    for members, bbox in sectors:
        result = runtime.calculate("plan_search", {"members": members, "bbox": bbox})
        assert result["status"] == "succeeded", result
        plan = runtime.submit(result["result_id"], members[0], runtime.episode)
        assert plan["status"] == "active"
    runtime.start()


def test_eight_search_detect_track_lost_reacquire(tmp_path):
    runtime = MissionRuntime(tmp_path/"scenario.sqlite")
    try:
        deploy_fleet(runtime)
        for _ in range(9000):
            runtime.tick()
            assert runtime.status == "running"
            if any(c["state"] == "confirmed" for c in runtime.contacts.values()):
                break
        contact = next(c for c in runtime.contacts.values() if c["state"] == "confirmed")
        member = min(runtime.uuvs, key=lambda u: math.dist(u["pose"][:2], [contact["x"], contact["y"]]))["id"]
        candidate = runtime.calculate("plan_tracking", {"members": [member], "contact_id": contact["contact_id"]})
        pending = runtime.submit(candidate["result_id"], "track", runtime.episode)
        assert pending["status"] == "pending_approval"
        runtime.decide(pending["plan_id"], True)
        before = runtime.sim_time
        for _ in range(3000):
            runtime.tick()
            assert runtime.status == "running"
        assert runtime.sim_time == before+600
        assert contact["state"] == "tracking"
        assert len(runtime.active) == 8
        assert sum(action["kind"] == "search" for action in runtime.active.values()) == 7
        runtime.sensor_enabled = False
        for _ in range(100):
            runtime.tick()
            if runtime.status != "running":
                break
        assert contact["state"] == "lost"
        assert runtime.status == "safety_paused"
        assert runtime.events[-1]["type"] == "safety_tracking_contact_lost"
        bbox = [max(100, contact["x"]-700), max(100, contact["y"]-700),
                min(3900, contact["x"]+700), min(3900, contact["y"]+700)]
        candidate = runtime.calculate("plan_search", {"members": [member], "mode": "reacquire", "bbox": bbox})
        assert candidate["status"] == "succeeded", candidate
        pending = runtime.submit(candidate["result_id"], "reacquire", runtime.episode)
        assert pending["status"] == "pending_approval"
        runtime.decide(pending["plan_id"], True)
        runtime.sensor_enabled = True
        runtime.start()  # Explicit human resume after protection pause.
        for _ in range(30):
            runtime.tick()
            assert runtime.status == "running"
        assert contact["state"] == "confirmed"
        assert len([e for e in runtime.events if e["type"] == "target_found"]) >= 2
    finally:
        runtime.close()


def test_four_hours_approved_search_without_model(tmp_path):
    runtime = MissionRuntime(tmp_path/"four-hours.sqlite")
    try:
        runtime.targets = []
        result = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
        runtime.submit(result["result_id"], "long-search", runtime.episode)
        runtime.start()
        for _ in range(72000):
            runtime.tick()
            assert runtime.status == "running"
            assert abs(runtime.uuvs[0]["curvature"]) <= 1/60
        assert runtime.sim_time == 14400
        assert sum(e["type"] == "search_complete" for e in runtime.events) >= 7
        assert len(runtime.agent_jobs) == 1  # Coalesced, no worker consuming.
    finally:
        runtime.close()
