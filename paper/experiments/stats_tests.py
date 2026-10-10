"""Paired Wilcoxon signed-rank tests on per-rep detection calls.

Compares the proposed method against every other configuration within each
case (paired by rep index), writing results/wilcoxon.csv. Implemented
without scipy so the venv needs no extra deps.
"""
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")


def wilcoxon(x, y):
    """Two-sided Wilcoxon signed-rank p-value (normal approx, ties handled).

    Returns (W, p). Exact enough for n>=10 pairs; zero diffs dropped."""
    d = np.asarray(x) - np.asarray(y)
    d = d[d != 0]
    n = len(d)
    if n < 5:
        return np.nan, np.nan
    ranks = _rank(np.abs(d))
    W = float(ranks[d > 0].sum())
    mu = n * (n + 1) / 4
    # tie correction
    _, cnt = np.unique(ranks, return_counts=True)
    tie = float((cnt ** 3 - cnt).sum())
    sd = np.sqrt(max(1e-12, (n * (n + 1) * (2 * n + 1) - tie / 2) / 24))
    z = (W - mu) / sd
    p = 2 * _norm_sf(abs(z))
    return W, p


def _rank(a):
    order = np.argsort(a)
    ranks = np.empty(len(a))
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and a[order[j + 1]] == a[order[i]]:
            j += 1
        ranks[order[i:j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    return ranks


def _norm_sf(z):
    import math
    return 0.5 * math.erfc(z / math.sqrt(2))


def main():
    df = pd.read_csv(os.path.join(RES, "metrics.csv"))
    rows = []
    for case in df["case"].unique():
        sub = df[df["case"] == case]
        ours = sub[sub["method"] == "本方法"].sort_values("rep")["calls"]
        for m in sub["method"].unique():
            if m == "本方法":
                continue
            other = sub[sub["method"] == m].sort_values("rep")["calls"]
            k = min(len(ours), len(other))
            W, p = wilcoxon(ours.values[:k], other.values[:k])
            rows.append({"case": case, "vs": m, "n_pairs": k,
                         "W": W, "p": p})
            print(f"{case} vs {m}: W={W:.1f} p={p}")
    pd.DataFrame(rows).to_csv(os.path.join(RES, "wilcoxon.csv"),
                              index=False)


if __name__ == "__main__":
    sys.exit(main())
