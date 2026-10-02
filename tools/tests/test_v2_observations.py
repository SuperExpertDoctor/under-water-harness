import math
import random

import numpy as np

from uuv_game.capabilities import observations


def test_passive_measurement_never_contains_target_position_or_range():
    sample = observations.measure([0, 0, 0], [100, 100, 0], "passive", random.Random(42))
    assert set(sample) == {"bearing_rad", "bearing_sigma", "mode"}
    assert abs(sample["bearing_rad"]-math.pi/4) < .1


def test_two_bearings_update_estimate_without_truth():
    contact = observations.initialize(520, 470, 0, 80)
    for t in range(1, 31):
        observations.predict(contact, t)
        for pose in ([250, 500, 0], [500, 250, 0]):
            observations.correct(contact, {"mode": "passive", "observer_pose": pose,
                "bearing_rad": math.atan2(500-pose[1], 500-pose[0]), "bearing_sigma": .03})
    assert math.dist([contact["x"], contact["y"]], [500, 500]) < 5
    assert np.linalg.eigvalsh(contact["covariance"]).min() > 0
    before = contact["uncertainty_m"]
    observations.predict(contact, 60)
    assert contact["uncertainty_m"] > before


def test_duplicate_prediction_does_not_change_estimate():
    contact = observations.initialize(100, 100, 0, 10)
    observations.predict(contact, 10)
    before = dict(contact)
    observations.predict(contact, 10)
    assert contact == before


def test_angle_wrap_does_not_jump_across_map():
    contact = observations.initialize(-100, .01, 0, 5)
    observations.correct(contact, {"mode": "passive", "observer_pose": [0, 0, 0],
        "bearing_rad": -math.pi+.001, "bearing_sigma": .03})
    assert abs(contact["y"]) < 1
