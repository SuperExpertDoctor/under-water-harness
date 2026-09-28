import json
from pathlib import Path

from uuv_game import config


def test_repository_settings_are_loaded_from_configs_directory():
    source = Path(__file__).resolve().parents[2] / "configs" / "uuv_game.json"
    settings = json.loads(source.read_text(encoding="utf-8"))
    assert settings["simulation"]["speed"] == config.Config().speed
    assert settings["simulation"]["coverage_window_min"] == config.Config().coverage_window_min
    for section in ("planning", "cbs", "coverage", "partition", "tracking", "control", "adversary", "observations", "information"):
        assert config.algorithm_settings(section) == settings["algorithms"][section]


def test_explicit_configuration_keeps_per_run_overrides():
    assert config.Config(coverage_window_min=2).coverage_window_min == 2
    assert config.algorithm_settings("cbs")["separation_m"] == config.Config().separation


def test_behavioral_tuning_sections_are_explicit():
    settings = config.algorithm_settings
    assert settings("assignment")["maximum_uuvs"] == 8
    assert settings("planning")["connector_attempt_interval"] == 6
    assert settings("coverage")["strip_inset_extra_m"] == 16
    assert settings("partition")["fallback_revisit_fraction"] == 0.25
    assert settings("control")["separation_subdivision_limit"] == 5
    assert settings("cbs")["local_replan_window_s"] == 25
    assert settings("runtime")["periodic_review_s"] == 30
    assert settings("lifecycle")["exit_trigger_buffer_m"] == 500
    assert settings("scene")["target"]["id"] == "TARGET-1"
    assert settings("handover")["acquisition_streak_s"] == 3
