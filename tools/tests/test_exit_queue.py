"""Exit-queue serialization and handover relief deferral."""

import math

import pytest

from uuv_game.lifecycle import prepare_exits
from uuv_game.runtime import MissionRuntime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    instance = MissionRuntime(tmp_path / "queue.sqlite")
    instance.obstacles = []
    instance.standing_policy.update(energy_rotation=True, local_repair=True, plan_id="authorized-rotation")
    monkeypatch.setattr("uuv_game.lifecycle.repair_search", lambda *args, **kwargs: True)
    yield instance
    instance.close()


def test_stagger_offsets_exit_trigger_per_hull(runtime):
    boat = runtime.uuvs[1]
    boat["pose"] = [800, 1000, math.pi]
    runtime.active[boat["id"]] = {"kind": "search", "generation": 1}
    boat["remaining_range_m"] = 2101
    assert prepare_exits(runtime)
    assert runtime.active[boat["id"]]["kind"] == "search"
    boat["remaining_range_m"] = 2100
    assert prepare_exits(runtime)
    assert runtime.active[boat["id"]]["kind"] == "exit"
    assert runtime.active[boat["id"]]["exit_point"] == [0, 1000]


def test_queue_cap_defers_extra_hull_until_forced_floor(runtime):
    for boat in runtime.uuvs[:2]:
        runtime.active[boat["id"]] = {"kind": "exit", "phase": "exiting", "generation": 1}
    boat = runtime.uuvs[2]
    boat["pose"] = [800, 1000, math.pi]
    runtime.active[boat["id"]] = {"kind": "search", "generation": 1}
    boat["remaining_range_m"] = 2400
    assert prepare_exits(runtime)
    assert runtime.active[boat["id"]]["kind"] == "search"
    boat["remaining_range_m"] = 1350
    assert prepare_exits(runtime)
    assert runtime.active[boat["id"]]["kind"] == "exit"


def test_relief_deferral_survives_normal_trigger_until_abort_floor(runtime):
    boat = runtime.uuvs[0]
    boat["pose"] = [800, 1000, math.pi]
    runtime.active[boat["id"]] = {"kind": "track", "generation": 1,
        "contact_id": "CONTACT-1", "relief_member": "UUV-5"}
    boat["remaining_range_m"] = 1500
    assert prepare_exits(runtime)
    assert runtime.active[boat["id"]]["kind"] == "track"
    boat["remaining_range_m"] = 1200
    assert prepare_exits(runtime)
    assert runtime.active[boat["id"]]["kind"] == "exit"
