"""Supplementary experiments for the paper.

Studies (each writes CSV/JSON into results_sweep/<study>/):
  delta  : aging-threshold Delta sensitivity, moving case,
           Delta in {0.05,0.10,0.15,0.20,0.30}
  prior  : prior-quality robustness, static case — shifted hotspot,
           spurious lobe, confident-but-wrong prior, uniform prior
  scale  : domain-size scaling, static case — grid n in
           {40,60,80,100,120}, ours vs lawnmower
  llm    : LLM compile statistics — repeated calls on varied intel /
           intent texts, reporting schema-valid rate, clamp-hit rate
           and per-parameter spread

Usage: python sweep_experiments.py <study> [--reps N] [--resdir DIR]
"""
import argparse
import copy
import json
import os
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from field import EvidenceField, STATIC_LOBES, make_grid, prior_from_gaussians, \
    reference_pf
from al_dnn import ALRunner
from baselines import lawnmower
from llm_compiler import compile_prior, compile_intent, clamp_params
from run_experiments import (N_INIT, N_MC, MAX_ITERS, DRIFT_VEL, AGE_TAU,
                             LLM_DIR, boundary_iou)

prior_file = os.path.join(LLM_DIR, "prior_static.json")
intent_file = os.path.join(LLM_DIR, "intent_static.json")
prior_m_file = os.path.join(LLM_DIR, "prior_moving.json")

HERE = os.path.dirname(os.path.abspath(__file__))
SWEEP = os.path.join(HERE, "results_sweep")


def _params(**over):
    p = clamp_params({})
    p.update(over)
    return p


def _eval_row(case, method, rep, out, field_now, grid, shape):
    ref = reference_pf(field_now, None)
    p = out["p_est"]
    return {"case": case, "method": method, "rep": rep,
            "calls": out["calls"], "p_est": p, "ref": ref,
            "rel_err": abs(p - ref) / ref * 100 if ref else np.nan,
            "iou": boundary_iou(out["ghat"], grid, field_now, shape)
            if out.get("ghat") is not None else np.nan}


# ---------------- delta sweep (moving) ------------------------------------
def study_delta(reps, resdir):
    grid, shape = make_grid(n=100)
    prior_m = json.load(open(prior_m_file))
    comps = prior_m.get("comps")
    pw = prior_from_gaussians(grid, comps) if comps \
        else np.full(len(grid), 1 / len(grid))
    intent = json.load(open(intent_file)).get("clamped") or clamp_params({})
    rows = []
    for rep in range(reps):
        for d in [0.05, 0.10, 0.15, 0.20, 0.30, 0.45, 0.60, 0.80]:
            f = EvidenceField(STATIC_LOBES, tilt=(0.15, 0.1))
            par = _params(**intent)
            par["delta"] = d
            r = ALRunner(f, grid, pw, par, seed=3000 + rep)
            out = r.run(n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS,
                        drift_vel=DRIFT_VEL, age_tau=AGE_TAU)
            row = _eval_row("机动目标", f"Δ={d:.2f}", rep, out, f,
                            grid, shape)
            row["reverify_cells"] = out.get("reverify_cells", 0)
            rows.append(row)
            print(f"delta rep{rep} d={d}: calls={out['calls']} "
                  f"reverify={out.get('reverify_cells')}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(resdir, "metrics.csv"),
                              index=False)


# ---------------- prior robustness (static) --------------------------------
def _perturb_comps(comps, mode, rng):
    """Return prior comps distorted per mode."""
    comps = copy.deepcopy(comps)
    if mode == "偏移0.1":
        for c in comps:
            c["mu"] = (min(0.95, c["mu"][0] + 0.1), c["mu"][1])
    elif mode == "偏移0.2":
        for c in comps:
            c["mu"] = (min(0.95, c["mu"][0] + 0.2),
                       max(0.05, c["mu"][1] - 0.1))
    elif mode == "偏移0.3":
        for c in comps:
            c["mu"] = (min(0.95, c["mu"][0] + 0.3),
                       max(0.05, c["mu"][1] - 0.2))
    elif mode == "虚假热点":
        comps = comps + [{"mu": (0.15, 0.15), "cov": [[0.02, 0], [0, 0.02]],
                          "w": 1.5}]
    elif mode == "全域错报":
        comps = [{"mu": (0.2, 0.8), "cov": [[0.02, 0], [0, 0.02]], "w": 2.0}]
    return comps


def study_prior(reps, resdir):
    grid, shape = make_grid(n=100)
    prior = json.load(open(prior_file))
    comps0 = prior.get("comps")
    intent = json.load(open(intent_file)).get("clamped") or clamp_params({})
    modes = ["正确先验", "偏移0.1", "偏移0.2", "偏移0.3",
             "虚假热点", "全域错报", "均匀(无先验)"]
    rows = []
    for rep in range(reps):
        rng = np.random.default_rng(4000 + rep)
        for mode in modes:
            if mode == "正确先验":
                pw = prior_from_gaussians(grid, comps0)
                use_prior = True
            elif mode == "均匀(无先验)":
                pw = None
                use_prior = False
            else:
                pw = prior_from_gaussians(
                    grid, _perturb_comps(comps0, mode, rng))
                use_prior = True
            f = EvidenceField(STATIC_LOBES, tilt=(0.15, 0.1))
            r = ALRunner(f, grid, pw, intent, use_prior=use_prior,
                         seed=4000 + rep)
            out = r.run(n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS)
            rows.append(_eval_row("静态目标", mode, rep, out, f, grid, shape))
            print(f"prior rep{rep} {mode}: calls={out['calls']}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(resdir, "metrics.csv"),
                              index=False)


# ---------------- scale sweep (static) --------------------------------------
def study_scale(reps, resdir):
    intent = json.load(open(intent_file)).get("clamped") or clamp_params({})
    rows = []
    for n in [40, 60, 80, 100, 120]:
        grid, shape = make_grid(n=n)
        prior = json.load(open(prior_file))
        comps = prior.get("comps")
        pw = prior_from_gaussians(grid, comps) if comps \
            else np.full(len(grid), 1 / len(grid))
        for rep in range(reps):
            f = EvidenceField(STATIC_LOBES, tilt=(0.15, 0.1))
            r = ALRunner(f, grid, pw, intent, seed=5000 + rep)
            out = r.run(n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS)
            rows.append(_eval_row(f"N={n}²", "本方法", rep, out, f,
                                  grid, shape))
            f = EvidenceField(STATIC_LOBES, tilt=(0.15, 0.1))
            out2 = lawnmower(f, grid, None)
            rows.append(_eval_row(f"N={n}²", "全覆盖扫描", rep, out2, f,
                                  grid, shape))
            print(f"scale n={n} rep{rep}: al={out['calls']} "
                  f"cov={out2['calls']}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(resdir, "metrics.csv"),
                              index=False)


# ---------------- LLM compile statistics ------------------------------------
INTEL_VARIANTS = [
    """情报简报1：无人机在海域东南角礁区附近拍摄到疑似通气管航行迹象，
持续时间约3分钟。历史数据提示目标偏好深水航道。海况：南风3级。""",
    """情报简报2：岸基声呐阵列在西部海沟边缘捕获低频噪声特征，
信噪比低、未能持续跟踪。既往记录显示目标常在西部水域出没。""",
    """情报简报3：卫星红外影像显示海域中部存在异常温度梯度带，
与水下尾流特征相符。同期商船航线正常，无其他异常。""",
    """情报简报4：夜间巡逻艇在西北浅滩水域听到异常推进器噪声，
持续约8分钟后消失。该水域水深不足，目标通常不会进入。""",
    """情报简报5：连续三日反潜机在东部深水区投下声呐浮标，
两日零接触，一日获弱磁性异常信号。目标可能在东部水域低速活动。""",
    """情报简报6：水下警戒网在中部水道触发两次短暂报警，间隔4小时，
判断为低速通过目标。周边海况平静。""",
    """情报简报7：电子侦察截获海域北部的短促无线电突发信号，
与潜望镜状态通信特征一致。北部存在岛屿遮蔽区。""",
    """情报简报8：渔业观察员报告西南水域出现异常下泄流扰动和少量油迹，
可能与水下航行器活动相关。海况良好。""",
]

INTENT_VARIANTS = [
    "先对全海域做一次较快普查，重点细查东北深水水道；预算有限，节省燃油。",
    "时间窗口紧张，要求在两小时内给出区域安全声明，允许粗略但快速。",
    "目标疑似在东南部低速待机，集中兵力细查东南，兼顾北部水道。",
    "多艇协同，宁可多花时间也要把边界画准，置信度优先于速度。",
    "燃油紧张，优先小批量高频次探测，保守收敛。",
    "目标可能正在机动穿越中部水道，强调观测时效性与边界跟踪。",
    "上级要求对全域给出'无目标'的高置信声明，宁可多探测不能漏。",
    "先快速圈定高概率区，再组织精扫；允许探索与确认并行。",
]

INTENT_SITUATIONS = [
    "海域100x100格，4艇，燃油中等，时间充裕",
    "海域100x100格，4艇，燃油紧张，时间窗口2小时",
    "海域100x100格，6艇，目标机动中，要求时效优先",
    "海域100x100格，4艇，现有覆盖率35%，两个候选热点",
    "海域100x100格，8艇，夜间作业，能见度低",
]


def _clip_hits(raw, clamped):
    hits = []
    for k in ["p0", "p_min", "decay", "delta", "dual_ratio", "explore_w",
              "batch"]:
        if k in raw and abs(float(raw[k]) - float(clamped[k])) > 1e-9:
            hits.append(k)
    return hits


def study_llm(reps, resdir):
    api_key = os.environ.get("LONGCAT_API_KEY", "")
    rows = []
    for rep in range(reps):
        for i, txt in enumerate(INTEL_VARIANTS):
            row = {"kind": "prior", "rep": rep, "variant": i,
                   "valid_json": 0, "n_hotspots": np.nan,
                   "confidence": np.nan, "clamped_fields": ""}
            try:
                out = compile_prior(txt, api_key)
                raw = out["raw"]
                row.update(valid_json=1, n_hotspots=len(raw["hotspots"]),
                           confidence=raw.get("confidence", np.nan))
                ok = all(0.02 <= h["sigma"] <= 0.25 and 0.1 <= h["w"] <= 3
                         for h in raw["hotspots"])
                row["clamped_fields"] = "" if ok else "range"
            except Exception as e:
                row["error"] = str(e)[:80]
            rows.append(row)
            print(f"llm prior rep{rep} v{i}: {row}", flush=True)
        for i, txt in enumerate(INTENT_VARIANTS):
            row = {"kind": "intent", "rep": rep, "variant": i,
                   "valid_json": 0, "clamped_fields": ""}
            try:
                raw = compile_intent(txt, INTENT_SITUATIONS[i % len(
                    INTENT_SITUATIONS)], api_key)
                clamped = clamp_params(raw)
                row.update(valid_json=1,
                           clamped_fields=",".join(_clip_hits(raw, clamped)),
                           **{f"p_{k}": clamped[k] for k in clamped})
            except Exception as e:
                row["error"] = str(e)[:80]
            rows.append(row)
            print(f"llm intent rep{rep} v{i}: valid={row['valid_json']}",
                  flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(resdir, "llm_stats.csv"),
                              index=False)


# ---------------- observation-noise sensitivity (static) ---------------------
class _NoisyField:
    """Wrap EvidenceField: each probe returns the sign-flipped evidence value
    with probability p_flip (detection misclassification P_D/P_FA lumped)."""

    def __init__(self, field, p_flip, rng):
        self.f, self.p, self.rng = field, p_flip, rng

    def eval(self, pts):
        g = self.f.eval(pts)
        flip = self.rng.random(len(g)) < self.p
        return np.where(flip, -g, g)

    def drift(self, v):
        self.f.drift(v)


def study_noise(reps, resdir):
    grid, shape = make_grid(n=100)
    prior = json.load(open(prior_file))
    comps = prior.get("comps")
    pw = prior_from_gaussians(grid, comps) if comps \
        else np.full(len(grid), 1 / len(grid))
    intent = json.load(open(intent_file)).get("clamped") or clamp_params({})
    rows = []
    for p_flip in [0.0, 0.05, 0.10]:
        for rep in range(reps):
            rng = np.random.default_rng(6000 + rep)
            base = EvidenceField(STATIC_LOBES, tilt=(0.15, 0.1))
            nf = _NoisyField(base, p_flip, rng)
            r = ALRunner(nf, grid, pw, intent, seed=6000 + rep)
            out = r.run(n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS)
            rows.append(_eval_row("静态目标", f"误检率{p_flip:.2f}", rep,
                                  out, base, grid, shape))
            print(f"noise rep{rep} p={p_flip}: calls={out['calls']} "
                  f"err={rows[-1]['rel_err']:.1f}", flush=True)
    pd.DataFrame(rows).to_csv(os.path.join(resdir, "metrics.csv"),
                              index=False)


STUDIES = {"delta": study_delta, "prior": study_prior,
           "scale": study_scale, "llm": study_llm, "noise": study_noise}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("study", choices=list(STUDIES))
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--resdir", type=str, default=None)
    args = ap.parse_args()
    resdir = args.resdir or os.path.join(SWEEP, args.study)
    os.makedirs(resdir, exist_ok=True)
    STUDIES[args.study](args.reps, resdir)
    print("saved to", resdir)


if __name__ == "__main__":
    main()
