"""Figures + LaTeX table fragments for the supplementary sweep experiments.

Reads results_sweep/{delta,prior,scale,llm} and writes
figures/{fig_delta_sensitivity,fig_prior_robustness,fig_scale}.{pdf,png},
matching CSVs under figures/data/, plus latex/table fragments.
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import figstyle  # noqa

figstyle.apply()
SWEEP = os.path.join(HERE, "results_sweep")
FIG = os.path.join(HERE, "figures")
CSV = os.path.join(FIG, "data")
TEXDIR = os.path.join(HERE, "..", "latex")
os.makedirs(CSV, exist_ok=True)


def save(fig, name):
    fig.savefig(os.path.join(FIG, name + ".pdf"), bbox_inches="tight")
    fig.savefig(os.path.join(FIG, name + ".png"), dpi=200,
                bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


def fig_delta():
    df = pd.read_csv(os.path.join(SWEEP, "delta", "metrics.csv"))
    df["delta"] = df.method.str.extract(r"([\d.]+)").astype(float)
    rows = []
    for d, g_ in df.groupby("delta"):
        cm, cmad = figstyle.med_mad(g_.calls)
        im, imad = figstyle.med_mad(g_.iou)
        rows.append({"delta": d, "calls_m": cm, "calls_err": cmad,
                     "iou_m": im, "iou_err": imad,
                     "err_m": g_.rel_err.mean()})
    g = pd.DataFrame(rows).sort_values("delta")
    fig, ax = plt.subplots(figsize=(3.3, 2.3), constrained_layout=True)
    ax.errorbar(g.delta, g.calls_m, yerr=g.calls_err, fmt="o-",
                lw=1.2, ms=3.5, capsize=2.5, color="#D55E00",
                label="探测次数")
    ax.set_xlabel(r"证据老化权重阈值 $\Delta$")
    ax.set_ylabel(r"探测次数 $N_\mathrm{call}$")
    ax.set_ylim(0, 80)
    ax2 = ax.twinx()
    ax2.grid(False)
    ax2.errorbar(g.delta, g.iou_m, yerr=g.iou_err, fmt="s--",
                 lw=1.0, ms=3, capsize=2.5, color="#0072B2",
                 label="边界 IoU")
    ax2.set_ylabel("边界 IoU")
    ax2.set_ylim(0, 0.5)
    ax2.tick_params(axis="y", colors="#0072B2", labelsize=7.5)
    ax.axvline(0.10, ls=":", c="k", lw=0.8)
    ax.set_xlim(0.04, 0.31)
    ax.set_xticks([0.05, 0.10, 0.15, 0.20, 0.30])
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper center", framealpha=0.95,
              edgecolor="0.5", borderpad=0.3)
    save(fig, "fig_delta_sensitivity")
    g.to_csv(os.path.join(CSV, "fig_delta_sensitivity.csv"), index=False)


def fig_prior():
    df = pd.read_csv(os.path.join(SWEEP, "prior", "metrics.csv"))
    order = ["正确先验", "偏移0.1", "偏移0.2", "偏移0.3", "虚假热点",
             "全域错报", "均匀(无先验)"]
    rows = []
    for m in order:
        g_ = df[df.method == m]
        cm, cmad = figstyle.med_mad(g_.calls)
        rows.append({"method": m, "calls_m": cm, "calls_err": cmad,
                     "calls_mean": g_.calls.mean(),
                     "calls_std": g_.calls.std(), "iou_m": g_.iou.mean(),
                     "err_m": g_.rel_err.mean()})
    g = pd.DataFrame(rows)
    fig, ax = plt.subplots(figsize=(3.3, 2.4), constrained_layout=True)
    x = np.arange(len(g))
    cols = ["#D55E00" if m == "正确先验" else "#9E9E9E" for m in g.method]
    bars = ax.bar(x, g.calls_m, 0.62, yerr=g.calls_err, capsize=2.5,
                  color=cols, error_kw=dict(lw=0.8, capthick=0.8))
    for b, v, e in zip(bars, g.calls_m, g.calls_err):
        ax.text(b.get_x() + b.get_width() / 2, v + e, f"{v:.0f}",
                ha="center", va="bottom", fontsize=6.5)
    base = float(g.loc[g.method == "均匀(无先验)", "calls_m"].iloc[0])
    ax.axhline(base, ls="--", c="k", lw=0.8)
    ax.text(0.15, base + 3, "无先验基线（中位数）",
            fontsize=6, ha="left", va="bottom")
    ax.set_xticks(x)
    ax.set_xticklabels(g.method, rotation=28, ha="right", fontsize=7)
    ax.set_ylabel(r"探测次数 $N_\mathrm{call}$")
    ax.set_ylim(0, (g.calls_m + g.calls_err).max() * 1.32)
    save(fig, "fig_prior_robustness")
    g.to_csv(os.path.join(CSV, "fig_prior_robustness.csv"), index=False)


def fig_scale():
    df = pd.read_csv(os.path.join(SWEEP, "scale", "metrics.csv"))
    df["n"] = df.case.str.extract(r"(\d+)").astype(int)
    rows = []
    for (c_, m_), g_ in df.groupby(["case", "method"]):
        cm, cmad = figstyle.med_mad(g_.calls)
        rows.append({"case": c_, "method": m_, "calls_m": cm,
                     "calls_err": cmad,
                     "calls_mean": g_.calls.mean(),
                     "calls_std": g_.calls.std()})
    g = pd.DataFrame(rows)
    g["n"] = g.case.str.extract(r"(\d+)").astype(int)
    g["N"] = g.n ** 2
    fig, ax = plt.subplots(figsize=(3.3, 2.4), constrained_layout=True)
    for m, c in [("本方法", "#D55E00"), ("全覆盖扫描", "#000000")]:
        d = g[g.method == m].sort_values("N")
        ax.errorbar(d.N, d.calls_m, yerr=d.calls_err,
                    fmt="o-", lw=1.2, ms=3.5, capsize=2.5, color=c,
                    label=m)
    ax.plot(g.N.unique(), g.N.unique(), "k:", lw=0.9, label="$N$（参考斜率）")
    dc = g[g.method == "全覆盖扫描"].sort_values("N")
    if len(dc) and dc.calls_m.iloc[-1] < dc.calls_m.iloc[-2]:
        ax.annotate("$\\varepsilon$ 判据提前误停",
                    xy=(dc.N.iloc[-1], dc.calls_m.iloc[-1]),
                    xytext=(0.50, 0.52), textcoords="axes fraction",
                    fontsize=6.5,
                    arrowprops=dict(arrowstyle="->", lw=0.7))
    ax.set_xscale("log")
    ax.set_yscale("log")
    ns = sorted(g.N.unique())
    ax.set_xticks(ns)
    ax.set_xticklabels([f"${v//1000 if v>=1000 else v}\\times10^3$"
                        if v >= 1000 else str(v) for v in ns], fontsize=7,
                       rotation=30, ha="right")
    ax.minorticks_off()
    ax.set_xlabel("候选点数 $N$")
    ax.set_ylabel(r"探测次数 $N_\mathrm{call}$")
    ax.legend(loc="upper left", framealpha=0.95, edgecolor="0.5",
              borderpad=0.3)
    save(fig, "fig_scale")
    g.to_csv(os.path.join(CSV, "fig_scale.csv"), index=False)


def llm_table():
    df = pd.read_csv(os.path.join(SWEEP, "llm", "llm_stats.csv"))
    p = df[df.kind == "prior"]
    it = df[df.kind == "intent"]
    pvalid = p.valid_json.mean() * 100
    ivalid = it.valid_json.mean() * 100
    nclamp = (it.clamped_fields.fillna("").str.len() > 0).mean() * 100
    pcols = [c for c in it.columns if c.startswith("p_")]
    frag = [
        "% auto-generated by make_sweep_figures.py",
        "\\begin{tabular}{lccc}",
        "\\toprule",
        "参数 & 均值$\\pm$标准差 & 钳制触发率/\\% & 取值界 \\\\",
        "\\midrule",
    ]
    bounds = {"p0": "[0.2,0.8]", "p_min": "[0.02,0.30]",
              "decay": "[0.60,0.95]", "delta": "[0.02,0.30]",
              "dual_ratio": "[0.30,0.70]", "explore_w": "[0.50,1.50]",
              "batch": "[2,8]"}
    names = {"p0": "$p_0$", "p_min": "$p_{\\min}$", "decay": "$\\eta$",
             "delta": "$\\Delta$", "dual_ratio": "$r_d$",
             "explore_w": "$w_e$", "batch": "$B$"}
    for c in ["p0", "p_min", "decay", "delta", "dual_ratio", "explore_w",
              "batch"]:
        col = "p_" + c
        if col not in it.columns:
            continue
        v = it[col].dropna()
        hits = it.apply(lambda r: c in str(r.clamped_fields).split(",")
                        if pd.notna(r.get("clamped_fields")) else False,
                        axis=1).mean() * 100
        frag.append(f"{names[c]} & {v.mean():.2f}$\\pm${v.std():.2f} & "
                    f"{hits:.0f} & ${bounds[c]}$ \\\\")
    frag.append("\\bottomrule\n\\end{tabular}")
    tex = "\n".join(frag)
    open(os.path.join(TEXDIR, "llm_params_table.tex"), "w").write(tex)
    summary = {
        "prior_calls": len(p), "prior_valid_pct": float(pvalid),
        "prior_hotspots_mean": float(p.n_hotspots.mean()),
        "prior_conf_mean": float(p.confidence.mean()),
        "intent_calls": len(it), "intent_valid_pct": float(ivalid),
        "intent_clamp_pct": float(nclamp),
    }
    json.dump(summary, open(os.path.join(SWEEP, "llm", "summary.json"), "w"),
              ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "delta"):
        fig_delta()
    if which in ("all", "prior"):
        fig_prior()
    if which in ("all", "scale"):
        fig_scale()
    if which in ("all", "llm"):
        llm_table()
