"""Run all paper experiments and write results to results/*.csv.

Cases:
  case1 static target  : full / no-prior / no-intent / no-elim / lawnmower / random
  case2 moving target  : full / no-elim / lawnmower / random
Each stochastic config runs `reps` times with different seeds.
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
from baselines import lawnmower, random_search, info_gain
from llm_compiler import compile_prior, compile_intent, clamp_params

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
LLM_DIR = os.path.join(HERE, "llm_outputs")

os.makedirs(LLM_DIR, exist_ok=True)

GRID_N = 100
N_INIT, N_MC = 24, 20000
MAX_ITERS = 160
EPS_S, N_F = 0.02, 10
DRIFT_VEL = (0.0025, -0.002)    # per iteration, moving case (~0.2 domain over a mission)
AGE_TAU = 8.0                 # soft evidence-aging time constant (iters)
RE_INT = 20                   # closed-loop intent re-compile period (iters)

INTEL_STATIC = """海情通报（第7号）：7月12日04时许，巡逻机在本海域东北部
（东偏北深水水道附近）观察到疑似水下目标尾流，持续约6分钟。
历史AIS与往期侦察数据显示，目标惯常利用东部深水通道（水深大于80米）
由北向南机动；另在6月底曾于中东部海沟边缘获得一次弱声学接触，未确认。
海况：东北风4级，表层流东南向0.6节。请给出目标位置先验分布。"""

INTEL_MOVING = INTEL_STATIC + "\n补充：目标具备机动能力，预计以约3节速度向西南方向机动。"

INTENT_STATIC = "先对全海域做一次较快普查，重点细查东北深水水道；预算有限，节省燃油。"
INTENT_MOVING = "目标正在机动，优先保证观测时效性，快速确认热点并容忍更多探索。"


def boundary_iou(ghat, grid, field, shape):
    """IoU between predicted presence region (ghat<=0) and truth."""
    pred = ghat.reshape(shape) <= 0
    truth = field.eval(grid).reshape(shape) <= 0
    inter = (pred & truth).sum()
    union = (pred | truth).sum()
    return float(inter / union) if union else 1.0


def run_al(field, grid, prior_w, params, cfg, seed, shape):
    """cfg: use_prior/use_intent/use_elim flags."""
    f = copy.deepcopy(field)
    r = ALRunner(f, grid, prior_w, params,
                 use_prior=cfg.get("prior", True),
                 use_intent=cfg.get("intent", True),
                 use_elim=cfg.get("elim", True),
                 seed=seed)
    out = r.run(n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS,
                drift_vel=cfg.get("drift"), age_tau=cfg.get("age_tau"))
    ref = reference_pf(f, None) if cfg.get("prior_abs") else None
    return r, out


def evaluate(out, field, grid, shape, prior_w, ref_pf):
    ghat = out["ghat"]
    p = out["p_est"]
    err = abs(p - ref_pf) / ref_pf * 100 if ref_pf else np.nan
    iou = boundary_iou(ghat, grid, field, shape)
    return {"calls": out["calls"], "p_est": p, "rel_err": err, "iou": iou}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--start", type=int, default=0,
                    help="rep index offset (seed = 1000 + start + rep)")
    ap.add_argument("--resdir", type=str, default=None,
                    help="override results output dir (shard runs)")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="skip LLM calls, reuse saved llm_outputs")
    args = ap.parse_args()

    grid, shape = make_grid(n=GRID_N)
    api_key = os.environ.get("LONGCAT_API_KEY", "")

    # ---------- LLM semantic compile (real API calls, once per scenario) ---
    prior_file = os.path.join(LLM_DIR, "prior_static.json")
    intent_file = os.path.join(LLM_DIR, "intent_static.json")
    prior_m_file = os.path.join(LLM_DIR, "prior_moving.json")
    intent_m_file = os.path.join(LLM_DIR, "intent_moving.json")

    if not args.offline and api_key:
        for path, fn, txt in [
            (prior_file, compile_prior, INTEL_STATIC),
            (intent_file, lambda t, k: compile_intent(INTENT_STATIC,
                                                      "海域100x100格，4艇，燃油中等，时间充裕", k),
             INTENT_STATIC),
            (prior_m_file, compile_prior, INTEL_MOVING),
            (intent_m_file, lambda t, k: compile_intent(INTENT_MOVING,
                                                        "海域100x100格，4艇，目标机动中", k),
             INTENT_MOVING)]:
            try:
                out = fn(txt, api_key)
                json.dump(out, open(path, "w"), ensure_ascii=False, indent=1)
                print("LLM ok ->", path)
            except Exception as e:
                print("LLM call failed:", e)
    elif args.offline:
        print("offline mode: reuse saved LLM outputs")

    def _load_intent(path, fallback=None):
        if not os.path.exists(path):
            return fallback or clamp_params({})
        d = json.load(open(path))
        return d.get("clamped") or clamp_params(d)

    prior_static = json.load(open(prior_file)) if os.path.exists(prior_file) \
        else {"comps": None, "confidence": 0.5, "raw": None}
    intent_static = _load_intent(intent_file)
    prior_moving = json.load(open(prior_m_file)) if os.path.exists(prior_m_file) \
        else prior_static
    intent_moving = _load_intent(intent_m_file, intent_static)
    print("prior_static:", json.dumps(prior_static.get("raw"), ensure_ascii=False))
    print("intent_static:", json.dumps(intent_static, ensure_ascii=False))
    print("prior_moving:", json.dumps(prior_moving.get("raw"), ensure_ascii=False))
    print("intent_moving:", json.dumps(intent_moving, ensure_ascii=False))

    prior_w_s = prior_from_gaussians(grid, prior_static["comps"]) \
        if prior_static["comps"] else np.full(len(grid), 1 / len(grid))
    prior_w_m = prior_from_gaussians(grid, prior_moving["comps"]) \
        if prior_moving["comps"] else np.full(len(grid), 1 / len(grid))

    # closed-loop intent re-compilation: every RE_INT iterations the runner
    # re-translates the commander intent against the live situation. Calls
    # and results are logged to results/llm_updates.json for provenance;
    # in --offline mode the same saved updates are replayed.
    saved_updates = {}
    llm_updates = []

    def make_recompile(case, method, rep, intent_text):
        def fn(sit):
            key = (case, method, rep, sit["iter"])
            if args.offline:
                u = saved_updates.get(key)
                if u is not None:
                    llm_updates.append(u)
                    return u["clamped"]
                return None
            situation = (f"迭代{sit['iter']}，累计探测{sit['calls']}次，"
                         f"样本库{sit['n_doe']}点，存在域命中{sit['n_hits']}处，"
                         f"当前存在概率估计{sit['p_est']:.4f}")
            try:
                raw = compile_intent(intent_text, situation, api_key)
                p = clamp_params(raw)
            except Exception as e:
                print("recompile failed:", e)
                return None
            llm_updates.append({"case": case, "method": method, "rep": rep,
                                "iter": sit["iter"], "situation": situation,
                                "raw": raw, "clamped": p})
            return p
        return fn

    field_static = EvidenceField(STATIC_LOBES, tilt=(0.15, 0.1))
    ref_pf_s = reference_pf(field_static, None)
    field_moving = EvidenceField(STATIC_LOBES, tilt=(0.15, 0.1))
    ref_pf_m = reference_pf(field_moving, None)  # t=0 reference; drift evaluated vs final field below
    print(f"ref Pf static={ref_pf_s:.5f} moving(t0)={ref_pf_m:.5f}")

    RES = args.resdir or globals()["RES"]
    os.makedirs(RES, exist_ok=True)
    updates_path = os.path.join(RES, "llm_updates.json")
    if args.offline and os.path.exists(updates_path):
        for u in json.load(open(updates_path)):
            saved_updates[(u["case"], u["method"], u["rep"], u["iter"])] = u
    reps = 1 if args.smoke else args.reps
    rows = []
    curves = {}
    maps = {}

    def rec(name, case, rep, out, field_now):
        ref = reference_pf(field_now, None)
        p = out["p_est"]
        err = abs(p - ref) / ref * 100 if ref else np.nan
        iou = boundary_iou(out.get("ghat"), grid, field_now, shape) \
            if out.get("ghat") is not None else np.nan
        rows.append({"case": case, "method": name, "rep": rep,
                     "calls": out["calls"], "p_est": p, "ref": ref,
                     "rel_err": err, "iou": iou,
                     "iters": out.get("iters"),
                     "converged": out.get("converged")})
        key = f"{case}|{name}"
        curves.setdefault(key, []).append(
            {"x": out["call_hist"], "y": out["pf_hist"]})
        if rep == 0 and out.get("ghat") is not None:
            maps[key] = {"ghat": out["ghat"].tolist(),
                         "doe": out["doe_pts"].tolist(),
                         "doe_g": out["doe_g"].tolist()}

    t0 = time.time()
    for rep in range(reps):
        seed = 1000 + args.start + rep
        rng = np.random.default_rng(seed)
        # --- case 1 static ---
        for name, cfg in [
            ("本方法", {"prior": True, "intent": True, "elim": True}),
            ("无先验", {"prior": False, "intent": True, "elim": True}),
            ("无意图", {"prior": True, "intent": False, "elim": True}),
            ("无剔除", {"prior": True, "intent": True, "elim": False}),
            ("单侧采样", {"prior": True, "intent": True, "elim": True,
                          "dual": False}),
            ("双无", {"prior": False, "intent": False, "elim": True}),
        ]:
            f = copy.deepcopy(field_static)
            r = ALRunner(f, grid, prior_w_s, intent_static,
                         use_prior=cfg["prior"], use_intent=cfg["intent"],
                         use_elim=cfg["elim"], use_dual=cfg.get("dual", True),
                         seed=seed,
                         recompile_fn=make_recompile(
                             "静态目标", name, rep, INTENT_STATIC),
                         re_int=RE_INT)
            out = r.run(n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS)
            rec(name, "静态目标", rep, out, f)
            print(f"  c1 rep{rep} {name}: calls={out['calls']} "
                  f"p={out['p_est']:.5f}")
        # baselines
        f = copy.deepcopy(field_static)
        rec("梳篦覆盖", "静态目标", rep, lawnmower(f, grid, None), f)
        f = copy.deepcopy(field_static)
        rec("信息增益", "静态目标", rep,
            info_gain(f, grid, np.random.default_rng(seed),
                      n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS,
                      batch=intent_static["batch"], seed=seed), f)
        f = copy.deepcopy(field_static)
        rec("随机采样", "静态目标", rep,
            random_search(f, grid, None, rng, cap=40000), f)
        # --- case 2 moving ---
        for name, cfg in [
            ("本方法", {"prior": True, "intent": True, "elim": True,
                        "drift": DRIFT_VEL, "age_tau": AGE_TAU}),
            ("无剔除", {"prior": True, "intent": True, "elim": False,
                        "drift": DRIFT_VEL, "age_tau": None}),
            ("无意图", {"prior": True, "intent": False, "elim": True,
                        "drift": DRIFT_VEL, "age_tau": AGE_TAU}),
            ("无先验", {"prior": False, "intent": True, "elim": True,
                        "drift": DRIFT_VEL, "age_tau": AGE_TAU}),
            ("单侧采样", {"prior": True, "intent": True, "elim": True,
                          "dual": False, "drift": DRIFT_VEL,
                          "age_tau": AGE_TAU}),
            ("双无", {"prior": False, "intent": False, "elim": True,
                      "drift": DRIFT_VEL, "age_tau": AGE_TAU}),
        ]:
            f = copy.deepcopy(field_moving)
            r = ALRunner(f, grid, prior_w_m, intent_moving,
                         use_prior=cfg["prior"], use_intent=cfg["intent"],
                         use_elim=cfg["elim"], use_dual=cfg.get("dual", True),
                         seed=seed,
                         recompile_fn=make_recompile(
                             "机动目标", name, rep, INTENT_MOVING),
                         re_int=RE_INT)
            out = r.run(n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS,
                        drift_vel=cfg["drift"], age_tau=cfg["age_tau"])
            rec(name, "机动目标", rep, out, f)
            print(f"  c2 rep{rep} {name}: calls={out['calls']} "
                  f"p={out['p_est']:.5f}")
        f = copy.deepcopy(field_moving)
        rec("梳篦覆盖", "机动目标", rep,
            lawnmower(f, grid, None, drift_vel=DRIFT_VEL), f)
        f = copy.deepcopy(field_moving)
        rec("信息增益", "机动目标", rep,
            info_gain(f, grid, np.random.default_rng(seed),
                      n_init=N_INIT, n_mc=N_MC, max_iters=MAX_ITERS,
                      batch=intent_moving["batch"], drift_vel=DRIFT_VEL,
                      seed=seed), f)
        f = copy.deepcopy(field_moving)
        rec("随机采样", "机动目标", rep,
            random_search(f, grid, None, rng, cap=40000,
                          drift_vel=DRIFT_VEL), f)
        print(f"rep {rep} done, elapsed {time.time()-t0:.0f}s", flush=True)

    json.dump(llm_updates, open(updates_path, "w"),
              ensure_ascii=False, indent=1)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(RES, "metrics.csv"), index=False)
    json.dump(curves, open(os.path.join(RES, "curves.json"), "w"))
    json.dump(maps, open(os.path.join(RES, "maps.json"), "w"))
    np.save(os.path.join(RES, "grid.npy"), grid)
    # save scenario fields for figure generation
    json.dump({"static": STATIC_LOBES,
               "prior_static": prior_static["comps"],
               "prior_moving": prior_moving["comps"]},
              open(os.path.join(RES, "scenario.json"), "w"))
    print(df.groupby(["case", "method"])[["calls", "rel_err", "iou"]]
          .agg(["mean", "std"]).round(3))
    print("saved to", RES)


if __name__ == "__main__":
    main()
