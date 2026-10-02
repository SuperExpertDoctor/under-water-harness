"""Telemetry metrics use coverage evidence and durable actual handoff starts."""

import copy

import pytest

from test_v2_handover import runtime as handover_runtime
from uuv_game.capabilities.handover import prepare_handover
from uuv_game.runtime import MissionRuntime


@pytest.fixture
def mission(tmp_path):
    runtime = MissionRuntime(tmp_path / "metrics.sqlite")
    yield runtime
    runtime.close()


def test_unobserved_coverage_and_no_handoffs_have_explicit_unknown_rates(mission):
    metrics = mission.frame()["mission_metrics"]
    assert metrics["unscanned_cells"] == 1584
    assert metrics["recent_coverage_pct"] == 0
    assert metrics["revisit_timeliness_pct"] is None
    assert metrics["handoff_attempts"] == 0
    assert metrics["handoff_success_rate"] is None


def test_coverage_metrics_share_searchable_mask_and_thirty_minute_window(mission):
    mission.sim_time = 2000
    mission.scan_times[0][0] = 200
    mission.scan_times[0][1] = 199
    mission.scan_times[0][2] = 2000
    mission.scan_times[25][17] = 2000  # Obstacle-intersecting cell is not searchable.
    before = copy.deepcopy(mission.scan_times)
    metrics = mission.frame()["mission_metrics"]
    assert metrics["unscanned_cells"] == 1581
    assert metrics["recent_coverage_pct"] == pytest.approx(100*2/1584)
    assert metrics["revisit_timeliness_pct"] == pytest.approx(100*2/3)
    assert mission.scan_times == before


def test_handoff_rate_includes_started_but_unfinished_attempts(mission):
    mission.metrics.update(handoff_attempts=4, handoff_count=1)
    assert mission.frame()["mission_metrics"]["handoff_success_rate"] == 25


def test_handoff_attempts_increment_only_after_actual_committed_start(handover_runtime):
    runtime = handover_runtime
    assert prepare_handover(runtime)
    assert runtime.metrics["handoff_attempts"] == 1
    assert runtime.frame()["mission_metrics"]["handoff_success_rate"] == 0
    assert not prepare_handover(runtime)
    assert runtime.metrics["handoff_attempts"] == 1
    runtime.save()
    assert runtime.store.load()["metrics"]["handoff_attempts"] == 1


def test_failed_handoff_commit_rolls_back_attempt_count(handover_runtime, monkeypatch):
    runtime = handover_runtime
    original = runtime.event

    def fail_start(kind, data):
        if kind == "tracking_handoff_started":
            raise RuntimeError("injected event failure")
        return original(kind, data)

    monkeypatch.setattr(runtime, "event", fail_start)
    with pytest.raises(RuntimeError, match="injected event failure"):
        prepare_handover(runtime)
    assert runtime.metrics["handoff_attempts"] == 0
    assert not any(event["type"] == "tracking_handoff_started" for event in runtime.events)


@pytest.mark.parametrize("history", ["complete", "truncated", "gap", "empty"])
def test_legacy_handoff_denominator_requires_complete_event_history(tmp_path, history):
    path = tmp_path / "legacy-metrics.sqlite"
    runtime = MissionRuntime(path)
    runtime.metrics.pop("handoff_attempts", None)
    if history != "empty":
        runtime.event("tracking_handoff_started", {})
        runtime.event("tracking_handoff_completed", {})
        runtime.event("tracking_handoff_started", {})
        runtime.metrics["handoff_count"] = 1
    if history == "truncated":
        runtime.events = runtime.events[1:]
    elif history == "gap":
        runtime.events.pop(1)
    runtime.close()
    restored = MissionRuntime(path)
    expected = 2 if history == "complete" else 0 if history == "empty" else None
    assert restored.metrics["handoff_attempts"] == expected
    assert restored.frame()["mission_metrics"]["handoff_success_rate"] == (50 if history == "complete" else None)
    restored.close()
    reopened = MissionRuntime(path)
    assert reopened.metrics["handoff_attempts"] == expected
    reopened.close()


def test_unknown_legacy_handoff_denominator_stays_unknown_after_new_start(handover_runtime):
    runtime = handover_runtime
    runtime.metrics["handoff_attempts"] = None
    assert prepare_handover(runtime)
    assert runtime.metrics["handoff_attempts"] is None
    assert runtime.frame()["mission_metrics"]["handoff_success_rate"] is None
