"""Belief-map DNN surrogate + active-learning boundary search (AL-DNN).

Mirrors the reference algorithm (Zhai et al.): two-sided top-p screening
near the confidence boundary, shrinking screen ratio p_k, Maximin spacing
inside the candidate subset, Delta sample elimination, incremental weight
inheritance, and a cov-based stopping rule on the P_exist estimate.
Extended to batch sampling (B probes/iter = multi-UUV) and evidence aging
for moving targets.
"""
import copy
import numpy as np
import torch
import torch.nn as nn


class BeliefNet(nn.Module):
    def __init__(self, in_dim=2, hidden=(24, 24, 24)):
        super().__init__()
        layers, d = [], in_dim
        for h in hidden:
            layers += [nn.Linear(d, h), nn.SiLU()]  # Swish
            d = h
        layers += [nn.Linear(d, 1)]
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        return self.net(x).squeeze(-1)


class Surrogate:
    """DNN surrogate for the evidence field with incremental updates."""

    def __init__(self, hidden=(24, 24, 24), lr=3e-3, wd=1e-3, max_epochs=400,
                 seed=0):
        torch.manual_seed(seed)
        self.hidden = tuple(hidden)
        self.seed = seed
        self.model = BeliefNet(hidden=self.hidden)
        self.lr, self.wd, self.max_epochs = lr, wd, max_epochs
        self.mu = np.zeros(2)
        self.sd = np.ones(2)
        self.gmu, self.gsd = 0.0, 1.0

    def _train(self, X, Y, warm, w_extra=None):
        Xn = (X - self.mu) / self.sd
        Yn = (Y - self.gmu) / self.gsd
        Xt = torch.tensor(Xn, dtype=torch.float32)
        Yt = torch.tensor(Yn, dtype=torch.float32)
        # weight samples near the boundary higher so scattered positives do
        # not pull the fitted surface back above zero between hit points
        Wt = 1.0 / (np.abs(Yn) + 0.25)
        if w_extra is not None:
            w_extra = np.asarray(w_extra, float)
            Wt = Wt * (w_extra / (w_extra.mean() + 1e-12))
        Wt = torch.tensor(Wt, dtype=torch.float32)
        if not warm:
            torch.manual_seed(self.seed)
            self.model = BeliefNet(hidden=self.hidden)
        opt = torch.optim.AdamW(self.model.parameters(), lr=self.lr,
                                weight_decay=self.wd)
        sched = torch.optim.lr_scheduler.ReduceLROnPlateau(
            opt, factor=0.8, patience=50)
        # best state by EMA-smoothed train loss with plateau early stop;
        # a strided |G| validation split is degenerate at small DoE sizes
        best_loss, best_state, ema = np.inf, None, None
        stall = 0
        for ep in range(self.max_epochs):
            self.model.train()
            opt.zero_grad()
            loss = (Wt * (self.model(Xt) - Yt) ** 2).mean()
            loss.backward()
            opt.step()
            vl = loss.item()
            ema = vl if ema is None else 0.9 * ema + 0.1 * vl
            sched.step(ema)
            if ema < best_loss * (1 - 1e-3):
                best_loss, stall = ema, 0
                best_state = copy.deepcopy(self.model.state_dict())
            else:
                stall += 1
                if stall >= 200:
                    break
        if best_state is not None:
            self.model.load_state_dict(best_state)
        return best_loss

    def fit(self, X, Y, warm=True, w_extra=None):
        X, Y = np.asarray(X, float), np.asarray(Y, float)
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        self.gmu, self.gsd = Y.mean(), Y.std() + 1e-9
        return self._train(X, Y, warm, w_extra)

    def predict(self, X):
        X = np.asarray(X, float)
        Xn = (X - self.mu) / self.sd
        self.model.eval()
        with torch.no_grad():
            out = []
            for i in range(0, len(Xn), 8192):
                t = torch.tensor(Xn[i:i + 8192], dtype=torch.float32)
                out.append(self.model(t).numpy())
        return np.concatenate(out) * self.gsd + self.gmu


def maximin_select(cands, existing, n):
    """Greedy maximin: iteratively pick the candidate farthest from
    (existing ∪ already picked). Returns indices into cands."""
    cands = np.asarray(cands, float)
    existing = np.asarray(existing, float).reshape(-1, 2)
    picked = []
    pool = np.arange(len(cands))
    d2 = np.min(((cands[:, None, :] - existing[None, :, :]) ** 2).sum(-1), axis=1)
    for _ in range(min(n, len(cands))):
        i = pool[np.argmax(d2[pool])]
        picked.append(i)
        nd = ((cands - cands[i]) ** 2).sum(-1)
        d2 = np.minimum(d2, nd)
        pool = pool[pool != i]
    return picked


def doe_init(grid, n_init, rng, prior_w=None):
    """Initial design: plain Maximin, or prior-weighted (LLM-informed).

    Prior-weighted version samples a candidate pool ∝ prior then Maximin-
    selects inside it — coverage biased toward high-value water.
    """
    if prior_w is None:
        start = rng.integers(len(grid))
        return np.array([grid[i] for i in maximin_select(grid, grid[start:start + 1], n_init)])
    pool = rng.choice(len(grid), size=min(6 * n_init, len(grid)),
                      replace=False, p=prior_w)
    cands = grid[pool]
    first = rng.integers(len(cands))
    idx = maximin_select(cands, cands[first:first + 1], n_init)
    return cands[idx]


def mc_sample(grid, prior_w, n, rng):
    idx = rng.choice(len(grid), size=n, p=prior_w) if prior_w is not None \
        else rng.choice(len(grid), size=n)
    return grid[idx]


def two_sided_subset(ghat_c, p_k, dual_ratio):
    """Split candidates into predicted-presence / cleared sides, keep the
    top fraction nearest the boundary on each side (per dual_ratio)."""
    neg = np.where(ghat_c <= 0)[0]
    pos = np.where(ghat_c > 0)[0]
    n_neg = max(1, int(len(neg) * p_k)) if len(neg) else 0
    n_pos = max(1, int(len(pos) * p_k)) if len(pos) else 0
    keep_n = neg[np.argsort(np.abs(ghat_c[neg]))[:n_neg]] if len(neg) else np.array([], int)
    keep_p = pos[np.argsort(np.abs(ghat_c[pos]))[:n_pos]] if len(pos) else np.array([], int)
    return np.concatenate([keep_n, keep_p]), keep_n, keep_p


class ALRunner:
    """Active-learning boundary search runner.

    params (LLM-compiled or fixed): p0, p_min, decay, delta, dual_ratio,
    batch (probes per iteration = number of UUVs), explore_w scales the
    candidate pool kept by p_k.
    """

    def __init__(self, field, grid, prior_w, params, use_prior=True,
                 use_intent=True, use_elim=True, use_dual=True, seed=0,
                 recompile_fn=None, re_int=0):
        self.field = field
        self.grid = grid
        self.prior_w = prior_w
        self.p = params
        self.use_prior, self.use_intent, self.use_elim = use_prior, use_intent, use_elim
        self.use_dual = use_dual
        self.rng = np.random.default_rng(seed)
        self.sur = Surrogate(seed=seed)
        self.ages = None  # observation age per DoE point (evidence aging)
        self.iter = 0
        # closed-loop intent: every `re_int` iterations the runner hands the
        # current situation to `recompile_fn` (LLM) and adopts the returned
        # bounded parameters; re_int=0 keeps the mission-level (once) compile
        self.recompile_fn, self.re_int = recompile_fn, re_int

    def _params_now(self):
        """Effective params this iteration (fixed when no LLM intent)."""
        if self.use_intent:
            return self.p
        return {"p0": 0.4, "p_min": 0.05, "decay": 0.8, "delta": 0.1,
                "dual_ratio": 0.5, "batch": self.p.get("batch", 4),
                "explore_w": 1.0}

    def run(self, n_init=16, max_iters=160, n_mc=20000, n_f=10, eps_s=0.02,
            drift_vel=None, age_tau=None, min_iters=30, verbose=False):
        rng = self.rng
        pw = self.prior_w if self.use_prior else None
        DoE = doe_init(self.grid, n_init, rng, pw)
        Gd = self.field.eval(DoE)
        ages = np.zeros(n_init)
        Smc = mc_sample(self.grid, None, n_mc, rng)  # uniform eval set
        pf_hist, call_hist = [], []
        calls = n_init  # init probes are real measurements
        converged = False
        for it in range(max_iters):
            self.iter = it
            if drift_vel is not None and it > 0:
                self.field.drift(drift_vel)
            # evidence aging: observation weights decay with age — stale
            # labels fade instead of vanishing or poisoning at full weight
            w_extra = (np.exp(-ages / age_tau)
                       if (self.use_elim and age_tau is not None) else None)
            self.sur.fit(DoE, Gd, warm=(it > 0), w_extra=w_extra)
            p_exist = float((self.sur.predict(Smc) <= 0).mean())
            pf_hist.append(p_exist)
            call_hist.append(calls)
            if len(pf_hist) >= n_f:
                h = np.array(pf_hist[-n_f:])
                eps = h.std() / (abs(h.mean()) + 1e-12)
                seen = bool((np.asarray(Gd) <= 0).any())
                # consistent stop: stable estimate AND (enough effort, or a
                # positive estimate anchored by a real presence hit). If DoE
                # holds presence evidence but P=0 the model contradicts its
                # own data — keep refining instead of declaring clear.
                if eps <= eps_s and (it >= min_iters
                                     or (seen and p_exist > 1e-6)):
                    converged = True
                    break
            # closed-loop semantic compile: re-translate commander intent
            # against the live situation every re_int iterations
            if (self.recompile_fn is not None and self.use_intent
                    and self.re_int and it > 0 and it % self.re_int == 0):
                newp = self.recompile_fn({
                    "iter": it, "calls": calls, "n_doe": len(DoE),
                    "n_hits": int((np.asarray(Gd) <= 0).sum()),
                    "p_est": p_exist})
                if newp:
                    self.p.update(newp)
            par = self._params_now()
            ghat_c = self.sur.predict(self.grid)
            p_k = max(par["p_min"], par["p0"] * par["decay"] ** it)
            if self.use_dual:
                scs, keep_n, keep_p = two_sided_subset(
                    ghat_c, p_k, par["dual_ratio"])
            else:
                # single-sided ablation: top-p_k nearest boundary only
                scs = np.argsort(np.abs(ghat_c))[
                    :max(1, int(len(ghat_c) * p_k))]
                keep_n, keep_p = scs, np.array([], int)
            if len(scs) == 0:
                scs = np.argsort(np.abs(ghat_c))[:50]
            if self.use_elim and age_tau is not None and len(ages):
                # stale "clear" conclusions (decay weight < delta) return
                # to the pending set for re-verification; presence labels
                # are only down-weighted in the fit, never dropped
                w_old = np.exp(-np.asarray(ages, float) / age_tau)
                stale_pts = np.asarray(DoE)[
                    (np.asarray(Gd) <= 0) & (w_old < par["delta"])]
                if len(stale_pts):
                    d2 = ((self.grid[None] - stale_pts[:, None]) ** 2).sum(-1)
                    stale_cells = np.unique(d2.argmin(1))
                    keep_n = np.union1d(keep_n, stale_cells)
            # batch = confirm + clear + a reserved global-exploration share so
            # a cold start (no presence hits yet) cannot starve the search
            B = par["batch"]
            if not (np.asarray(Gd) <= 0).any():
                b_explore = max(1, B // 2)
            else:
                b_explore = int(np.clip(
                    round(B * 0.25 * par["explore_w"]), 1, B // 2))
            Bb = B - b_explore
            b_n = (int(round(Bb * par["dual_ratio"]))
                   if self.use_dual else Bb)
            b_p = Bb - b_n
            sel = []
            for side, b in ((keep_n, b_n), (keep_p, b_p)):
                side_ok = side[np.isin(side, scs)] if len(scs) else side
                if b and len(side_ok):
                    sel += list(maximin_select(self.grid[side_ok], DoE, b))
            if len(sel) < Bb:  # empty side falls back to the other side
                rest = np.concatenate([keep_n, keep_p])
                rest = rest[np.isin(rest, scs)] if len(scs) else rest
                need = Bb - len(sel)
                if len(rest):
                    sel += list(maximin_select(
                        self.grid[rest], np.vstack([DoE, self.grid[sel]])
                        if sel else DoE, need))
            # global exploration probes, maximin over the domain; with a
            # prior, draw the exploration pool from the prior first
            excl = np.vstack([DoE, self.grid[sel]]) if sel else DoE
            if pw is not None:
                eidx = rng.choice(len(self.grid), size=400, p=pw)
                sel += list(maximin_select(self.grid[eidx], excl, b_explore))
            else:
                sel += list(maximin_select(self.grid, excl, b_explore))
            if not sel:
                sel = maximin_select(self.grid[scs], DoE, B)
            new_pts = self.grid[sel]
            g_new = self.field.eval(new_pts)
            calls += len(new_pts)
            # evidence aging: stale observations expire (moving targets)
            ages = ages + 1
            DoE, Gd, ages = list(DoE), list(Gd), list(ages)
            for pt, gv in zip(new_pts, g_new):
                DoE.append(pt); Gd.append(gv); ages.append(0)
            DoE, Gd, ages = np.asarray(DoE), np.asarray(Gd), np.asarray(ages)
            if verbose:
                print(f"  it{it}: calls={calls} P={p_exist:.5f} doe={len(DoE)}")
        # reported estimate = the declared value at the stop point; the map
        # (ghat) is refit once more on the final DoE for the boundary figure
        p_final = pf_hist[-1]
        self.sur.fit(DoE, Gd, warm=True)
        ghat_all = self.sur.predict(self.grid)
        return {"calls": calls, "p_est": p_final, "pf_hist": pf_hist,
                "call_hist": call_hist, "ghat": ghat_all,
                "doe_pts": np.asarray(DoE), "doe_g": np.asarray(Gd),
                "iters": it + 1, "converged": converged}
