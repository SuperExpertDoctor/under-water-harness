"""Information is a read-only projection of scan and accepted contact evidence."""

import copy
import math

from fastapi.testclient import TestClient
import numpy as np
import pytest

from uuv_game.api import create_app
from uuv_game.config import Config
from uuv_game.observations import initialize, predict
from uuv_game.runtime import MissionRuntime


@pytest.fixture
def game(tmp_path):
    runtime = MissionRuntime(tmp_path / "information.sqlite")
    yield runtime
    runtime.close()


def contact(now=0, mode="active"):
    sample = {"mode": mode, "observer_pose": [750, 1950, 0], "observer_id": "UUV-1",
              "bearing_rad": 0, "bearing_sigma": math.radians(2), "time_s": now,
              "source": "sensor", "contact_id": "CONTACT-1"}
    if mode == "active":
        sample.update(range_m=300, range_sigma=4, x=1050, y=1950)
    return {**initialize(1050, 1950, now), "contact_id": "CONTACT-1", "state": "confirmed",
            "last_seen": now, "hits": [now], "samples": [sample], "observers": ["UUV-1"]}


def test_scan_half_life_and_obstacle_mask(game):
    game.scan_times[0][0] = 0
    game.scan_times[25][17] = 180
    game.sim_time = 180
    frame = game.frame()
    assert frame["info_matrix"][0][0] == pytest.approx(.5)
    assert frame["info_matrix"][25][17] == 0
    assert frame["information_model"] == {"schema": "uuv-information/v1", "scan_half_life_s": 180,
                                           "target_half_life_s": 60, "as_of_s": 180}
    assert frame["mission_metrics"]["recent_coverage_pct"] == pytest.approx(100/1584)


def test_real_active_scan_refreshes_and_paused_reads_are_immutable(game):
    game.uuvs[0]["pose"] = [2050, 2050, 0]
    game.active["UUV-1"] = {"kind": "search"}
    game.sim_time = 1
    game._observe()
    assert game.frame()["info_matrix"][20][17] == 1
    assert game.frame()["info_matrix"][22][19] == 0
    game.sim_time = 181
    assert game.frame()["info_matrix"][20][17] == pytest.approx(.5)
    game._observe()
    assert game.frame()["info_matrix"][20][17] == 1
    game.status = "paused"
    before = copy.deepcopy((game.scan_times, game.contacts, game.rng.getstate()))
    frame = game.frame()
    game.tick()
    assert game.frame()["info_matrix"] == frame["info_matrix"]
    assert (game.scan_times, game.contacts, game.rng.getstate()) == before


def test_information_counts_exclude_blocked_cells(game):
    game.scan_times[0][0] = 0
    frame = game.frame()
    counts = frame["information_cell_counts"]
    assert counts == {"white": 1, "gray": 0, "black": frame["searchable_cells"] - 1}
    assert sum(counts.values()) == frame["searchable_cells"] == 1584


def test_localized_evidence_has_neighborhood_without_scan_credit_or_truth(game):
    game.contacts["CONTACT-1"] = contact()
    saved = copy.deepcopy(game.scan_times)
    frame = game.frame()
    field = frame["target_info_matrix"]
    assert 0 < field[11][20] < field[10][20] <= 1
    assert frame["coverage_pct"] == 0
    assert game.scan_times == saved
    game.targets[0]["pose"] = [3800, 100, 2]
    assert game.frame()["target_info_matrix"] == field
    assert game.frame()["value_matrix"] == field


def test_target_age_halves_without_refresh_and_prediction_spreads(game):
    belief = contact()
    game.contacts["CONTACT-1"] = belief
    fresh = np.array(game.frame()["target_info_matrix"])
    game.sim_time = 60
    # A fixed covariance isolates evidence age from prediction uncertainty.
    belief["estimate_time"] = 60
    aged = np.array(game.frame()["target_info_matrix"])
    assert aged[10, 20] == pytest.approx(fresh[10, 20]/2)
    belief["estimate_time"] = 0
    predict(belief, 60)
    belief["state"] = "lost"
    spread = np.array(game.frame()["target_info_matrix"])
    assert spread.max() < aged.max()
    assert spread[13, 20]/spread[10, 20] > fresh[13, 20]/fresh[10, 20]
    before = copy.deepcopy(belief)
    assert np.array_equal(game.frame()["target_info_matrix"], spread)
    assert belief == before
    game.contacts["CONTACT-1"] = contact(60)
    assert np.array(game.frame()["target_info_matrix"]).max() > spread.max()


def test_single_passive_bearing_is_range_limited_fan_not_estimated_point(game):
    belief = contact(mode="passive")
    belief.update(state="tentative", x=3000, y=3000)
    game.contacts["CONTACT-1"] = belief
    field = np.array(game.frame()["target_info_matrix"])
    assert field[8, 20] > 0
    assert field[9, 20] > 0
    assert field[10, 20] > 0
    assert field[12, 20] == 0
    assert field[7, 19] == 0
    assert field[30, 10] == 0
    assert field.max() <= .35


def test_repeated_passive_hits_do_not_create_localization(game):
    belief = contact(mode="passive")
    belief.update(x=3000, y=3000, hits=[0, 1, 2])
    game.contacts["CONTACT-1"] = belief
    assert game.frame()["target_info_matrix"][30][10] == 0


def test_accepted_active_localization_survives_passive_sample_rollover(game):
    game.uuvs[0]["pose"] = [750, 1950, 0]
    game.targets[0]["pose"] = [1050, 1950, 0]
    game.active["UUV-1"] = {"kind": "search"}
    game.sim_time = 1
    game._observe()
    belief = next(iter(game.contacts.values()))
    assert belief["position_localized"] is True
    belief["samples"] = [contact(1, "passive")["samples"][0]]
    assert game.frame()["target_info_matrix"][10][19] > 0


def test_empty_and_masked_fields_are_finite_normalized(game):
    assert not np.array(game.frame()["target_info_matrix"]).any()
    game.contacts["CONTACT-1"] = contact()
    game.contacts["CONTACT-1"].update(x=2550, y=2250)
    game.scan_times[0][0] = 100  # A future timestamp cannot exceed normalized one.
    frame = game.frame()
    for key in ("info_matrix", "target_info_matrix"):
        values = np.array(frame[key])
        assert values.shape == (40, 40)
        assert np.isfinite(values).all()
        assert values.min() >= 0 and values.max() <= 1
        assert values[25, 17] == 0


@pytest.mark.parametrize("name", ["scan_half_life_s", "target_half_life_s"])
@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan, True])
def test_half_lives_are_positive_finite(name, value):
    with pytest.raises(ValueError, match=name):
        Config(**{name: value})


def test_checkpoint_reset_replay_http_and_live_websocket_parity(tmp_path):
    path = tmp_path / "transport.sqlite"
    app = create_app(path, ticking=False)
    with TestClient(app) as client:
        game = app.state.runtime
        game.scan_times[0][0] = 0
        game.contacts["CONTACT-1"] = contact()
        game.sim_time = 60
        state = client.get("/api/state").json()
        fields = ("info_matrix", "target_info_matrix", "information_model")
        with client.websocket_connect("/ws/live") as ws:
            streamed = ws.receive_json()
            assert all(streamed[key] == state[key] for key in fields)
            with game.lock:
                game.sim_time = 180
            streamed = ws.receive_json()
            state = client.get("/api/state").json()
            assert all(streamed[key] == state[key] for key in fields)
        game.store.frame(game.episode, state)
        replay = client.get("/api/replay", params={"file": game.episode}).json()
        assert all(replay["frames"][0][key] == state[key] for key in fields)
        game.save()
    restored = MissionRuntime(path)
    assert all(restored.frame()[key] == state[key] for key in fields)
    restored.reset()
    assert not np.array(restored.frame()["info_matrix"]).any()
    assert not np.array(restored.frame()["target_info_matrix"]).any()
    assert restored.frame()["information_model"]["as_of_s"] == 0
    restored.close()
