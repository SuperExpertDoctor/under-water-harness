"""Bandit layer for skill confidence — Beta posterior + UCB ranking.

Each distilled skill carries a Beta(alpha, beta) posterior over how
useful loading it proved to be. Every recorded use later yields one
reward in [0,1] derived from the mission-metric delta observed during
the reward window; the posterior mean is the displayed confidence and
the UCB score orders the library (exploit confidence, explore
under-used skills).
"""

import math


def confidence(alpha, beta):
    return alpha / max(1e-9, alpha + beta)


def ucb(entry, total_uses):
    """Upper-confidence bound: posterior mean plus an exploration bonus
    that decays as the skill itself accumulates uses."""
    return entry["confidence"] + math.sqrt(2 * math.log(max(1, total_uses + 1)) / (entry["uses"] + 1))


def update(entry, reward):
    reward = min(1.0, max(0.0, reward))
    entry["alpha"] += reward
    entry["beta"] += 1 - reward
    entry["confidence"] = round(confidence(entry["alpha"], entry["beta"]), 4)
    return reward


def reward(baseline, current, weights, window_s):
    """Score the observed metric movement after a skill was loaded.

    baseline/current are metric snapshots (see impl.metric_snapshot);
    weights come from the skill's category (each category values
    different outcomes — handover skills care about handoff success and
    lost seconds, coverage skills about coverage deltas). The neutral
    point is 0.5; improvements push toward 1, regressions toward 0.
    """
    window = max(1.0, window_s)
    d_cov = (current.get("recent_coverage_pct", 0.0) - baseline.get("recent_coverage_pct", 0.0)) / 100.0
    d_track = min(1.0, (current.get("effective_tracking_seconds", 0.0) - baseline.get("effective_tracking_seconds", 0.0)) / window)
    d_lost = min(1.0, (current.get("lost_seconds", 0.0) - baseline.get("lost_seconds", 0.0)) / window)
    d_hand = ((current.get("handoff_success_rate") or 0.0) - (baseline.get("handoff_success_rate") or 0.0)) / 100.0
    score = 0.5 + (weights.get("coverage", 0.0) * d_cov
                 + weights.get("track", 0.0) * d_track
                 - weights.get("lost", 0.0) * d_lost
                 + weights.get("handoff", 0.0) * d_hand)
    return round(min(1.0, max(0.0, score)), 4)
