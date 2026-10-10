"""Baseline search methods: lawnmower (raster) coverage and random sampling.

Both count every true-field probe as one detection call and stop under the
same rolling-window stability rule eps = std/mean(last n_f estimates) <= eps_s
as the AL method (or exhaust the area / hit the call cap).
"""
import numpy as np

from al_dnn import Surrogate, doe_init, mc_sample


def lawnmower(field, grid, prior_w, eps_s=0.02, n_f=10, batch=500,
              cap=None, drift_vel=None):
    """Raster scan over grid cells; each cell is one probe. Pf estimate is
    updated per batch on the probed prefix (prior-weighted)."""
    order = np.arange(len(grid))
    pf_hist, call_hist, probes = [], [], []
    calls = 0
    cap = cap or len(grid)
    for i0 in range(0, len(order), batch):
        idx = order[i0:i0 + batch]
        if calls >= cap:
            break
        pts = grid[idx]
        probes.append((pts, field.eval(pts) <= 0))
        calls += len(pts)
        if drift_vel is not None:
            field.drift(drift_vel)  # one planning cycle per batch
        allmask = np.concatenate([m for _, m in probes])
        allpts = np.concatenate([p for p, _ in probes])
        w = prior_w[idx] if prior_w is not None else np.ones(len(idx))
        pf = float(allmask.astype(float).mean())  # unweighted for raster
        pf_hist.append(pf); call_hist.append(calls)
        if len(pf_hist) >= n_f:
            h = np.array(pf_hist[-n_f:])
            if h.std() / (abs(h.mean()) + 1e-12) <= eps_s:
                break
    return {"calls": calls, "p_est": pf_hist[-1] if pf_hist else 0.0,
            "pf_hist": pf_hist, "call_hist": call_hist}


def info_gain(field, grid, rng, n_init=24, n_mc=20000, max_iters=160,
              eps_s=0.02, n_f=10, batch=4, min_iters=30, drift_vel=None,
              s=0.5, seed=0):
    """Greedy entropy (uncertainty-sampling) baseline.

    Same DNN surrogate and same eps stopping rule as the AL method — the
    difference is the acquisition: each iteration probes the `batch` grid
    points whose predicted presence probability is closest to 0.5 (max
    Bernoulli entropy of the presence indicator). No semantic prior, no
    two-sided quota, no spacing, no evidence aging.
    """
    sur = Surrogate(seed=seed)
    DoE = doe_init(grid, n_init, rng, None)  # uniform Maximin init
    Gd = field.eval(DoE)
    Smc = mc_sample(grid, None, n_mc, rng)
    pf_hist, call_hist = [], []
    calls = n_init
    converged = False
    for it in range(max_iters):
        if drift_vel is not None and it > 0:
            field.drift(drift_vel)
        sur.fit(DoE, Gd, warm=(it > 0))
        p_exist = float((sur.predict(Smc) <= 0).mean())
        pf_hist.append(p_exist)
        call_hist.append(calls)
        if len(pf_hist) >= n_f:
            h = np.array(pf_hist[-n_f:])
            eps = h.std() / (abs(h.mean()) + 1e-12)
            seen = bool((np.asarray(Gd) <= 0).any())
            if eps <= eps_s and (it >= min_iters
                                 or (seen and p_exist > 1e-6)):
                converged = True
                break
        ghat = sur.predict(grid)
        # presence probability via logistic calibration of the surrogate
        p = 1.0 / (1.0 + np.exp(ghat / s))
        ent = -(p * np.log(p + 1e-12) + (1 - p) * np.log(1 - p + 1e-12))
        sel = np.argsort(-ent)[:batch]
        g_new = field.eval(grid[sel])
        calls += len(sel)
        DoE = np.vstack([DoE, grid[sel]])
        Gd = np.concatenate([Gd, g_new])
    sur.fit(DoE, Gd, warm=True)
    return {"calls": calls, "p_est": pf_hist[-1] if pf_hist else 0.0,
            "pf_hist": pf_hist, "call_hist": call_hist,
            "ghat": sur.predict(grid),
            "doe_pts": np.asarray(DoE), "doe_g": np.asarray(Gd),
            "iters": it + 1, "converged": converged}


def random_search(field, grid, prior_w, rng, eps_s=0.02, n_f=10,
                  batch=1000, cap=40000, drift_vel=None):
    """Uniform random probing; Pf = fraction of probes inside presence region."""
    pf_hist, call_hist = [], []
    hits, calls = 0, 0
    while calls < cap:
        idx = rng.choice(len(grid), size=batch)
        vals = field.eval(grid[idx]) <= 0
        hits += int(vals.sum()); calls += len(vals)
        if drift_vel is not None:
            field.drift(drift_vel)  # one planning cycle per batch
        pf_hist.append(hits / calls); call_hist.append(calls)
        if len(pf_hist) >= n_f:
            h = np.array(pf_hist[-n_f:])
            if h.std() / (abs(h.mean()) + 1e-12) <= eps_s:
                break
    return {"calls": calls, "p_est": pf_hist[-1] if pf_hist else 0.0,
            "pf_hist": pf_hist, "call_hist": call_hist}
