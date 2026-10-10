"""Generate paper figures (vector PDF + preview PNG) and CSV exports.

Journal style: Noto Serif CJK (宋体) for Chinese text, mathtext for latin,
(a)/(b) subfigure labels only, legends kept off the data area, numeric labels
on bars. Every figure also writes a CSV with the underlying data (journal
requires editable figures with Excel data).
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
RES = os.environ.get("RES_DIR", os.path.join(HERE, "results"))
FIG = os.path.join(HERE, "figures")
CSV = os.path.join(FIG, "data")
os.makedirs(FIG, exist_ok=True)
os.makedirs(CSV, exist_ok=True)

sys.path.insert(0, HERE)
from field import EvidenceField, STATIC_LOBES, prior_from_gaussians  # noqa
import figstyle  # noqa

figstyle.apply()
COLORS = figstyle.COLORS
CASE_COLORS = figstyle.CASE_COLORS
METHOD_ORDER = figstyle.METHOD_ORDER
ABL_ORDER = figstyle.ABL_ORDER
_d = figstyle._d
REF_P = 0.05423


def save(fig, name):
    fig.savefig(os.path.join(FIG, name + ".pdf"), bbox_inches="tight")
    fig.savefig(os.path.join(FIG, name + ".png"), dpi=200, bbox_inches="tight")
    plt.close(fig)
    print("wrote", name)


def load():
    df = pd.read_csv(os.path.join(RES, "metrics.csv"))
    curves = json.load(open(os.path.join(RES, "curves.json")))
    maps = json.load(open(os.path.join(RES, "maps.json")))
    grid = np.load(os.path.join(RES, "grid.npy"))
    sc = json.load(open(os.path.join(RES, "scenario.json")))
    return df, curves, maps, grid, sc


def field_eval(grid, lobes):
    f = EvidenceField(lobes, tilt=(0.15, 0.1))
    return f.eval(grid)


def fig_sampling_map(grid, maps, sc):
    """(a) true field + presence region; (b) LLM prior; (c) predicted
    boundary + DoE samples."""
    n = int(np.sqrt(len(grid)))
    Gt = field_eval(grid, STATIC_LOBES).reshape(n, n)
    m = maps["静态目标|本方法"]
    ghat = np.array(m["ghat"]).reshape(n, n)
    doe = np.array(m["doe"])
    doe_g = np.array(m["doe_g"])
    xs = grid[:, 0].reshape(n, n)
    ys = grid[:, 1].reshape(n, n)
    prior_w = prior_from_gaussians(grid, sc["prior_static"]).reshape(n, n)

    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.55),
                             constrained_layout=True)

    ax = axes[0]
    cf = ax.contourf(xs, ys, Gt, levels=18, cmap="RdYlBu_r")
    ax.contour(xs, ys, Gt, levels=[0], colors="k", linewidths=1.2)
    ax.set_title("(a) 真实证据场")
    cb = fig.colorbar(cf, ax=ax, shrink=0.8, pad=0.02)
    cb.ax.tick_params(labelsize=7)

    ax = axes[1]
    pc = ax.pcolormesh(xs, ys, prior_w, cmap="YlOrRd", shading="auto")
    ax.contour(xs, ys, Gt, levels=[0], colors="k", linewidths=1.2,
               linestyles="--")
    ax.set_title("(b) LLM 编译的先验分布")
    cb = fig.colorbar(pc, ax=ax, shrink=0.8, pad=0.02)
    cb.ax.tick_params(labelsize=7)

    ax = axes[2]
    cf = ax.contourf(xs, ys, ghat, levels=18, cmap="RdYlBu_r")
    ax.contour(xs, ys, Gt, levels=[0], colors="k", linewidths=1.2)
    ax.contour(xs, ys, ghat, levels=[0], colors="magenta", linewidths=1.2,
               linestyles="--")
    neg = doe_g <= 0
    ax.scatter(doe[neg, 0], doe[neg, 1], s=4, c="#34495E", alpha=0.45,
               linewidths=0, label="排除域样本")
    ax.scatter(doe[~neg, 0], doe[~neg, 1], s=16, c="#C0392B", marker="x",
               linewidths=0.9, label="存在域样本")
    ax.plot([], [], "k-", lw=1.2, label="真实边界")
    ax.plot([], [], "m--", lw=1.2, label="预测边界")
    ax.set_title("(c) 预测置信边界与探测样本")
    ax.legend(loc="upper left", frameon=True, framealpha=0.95,
              edgecolor="0.7", borderpad=0.3, labelspacing=0.25)
    cb = fig.colorbar(cf, ax=ax, shrink=0.8, pad=0.02)
    cb.ax.tick_params(labelsize=7)

    for ax in axes:
        ax.set_xlabel("$x$（归一化）")
        ax.set_ylabel("$y$（归一化）")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.set_aspect("equal")
    save(fig, "fig_sampling_map")
    pd.DataFrame({"x": grid[:, 0], "y": grid[:, 1],
                  "prior_w": prior_w.ravel(), "G_true": Gt.ravel(),
                  "G_pred": ghat.ravel()}).to_csv(
        os.path.join(CSV, "fig_sampling_map.csv"), index=False)
    pd.DataFrame({"x": doe[:, 0], "y": doe[:, 1], "G": doe_g}).to_csv(
        os.path.join(CSV, "fig_sampling_map_samples.csv"), index=False)


def _median_band(runs, grid):
    """Interpolate each rep onto a common calls grid, holding its final
    converged value past its own end; return median + IQR band curves."""
    ys = []
    for r in runs:
        x = np.asarray(r["x"], float)
        y = np.asarray(r["y"], float)
        ys.append(np.interp(grid, x, y, left=y[0], right=y[-1]))
    ys = np.asarray(ys)
    return (np.median(ys, 0), np.percentile(ys, 25, axis=0),
            np.percentile(ys, 75, axis=0))


def _convergence_panels(curves, case, fname, axb_methods):
    """(a) AL variants on linear calls axis; (b) baselines on log axis.
    All curves: mean over 10 reps with ±1σ band, each rep extended at its
    converged value so every curve spans the same x range."""
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.6),
                             constrained_layout=True)
    ax = axes[0]
    xmax = max(max(r["x"][-1] for r in curves[f"{case}|{name}"])
               for name in figstyle.CURVE_ORDER
               if f"{case}|{name}" in curves)
    grid = np.linspace(0, xmax, 400)
    for name in figstyle.CURVE_ORDER:
        key = f"{case}|{name}"
        if key not in curves:
            continue
        m, lo, hi = _median_band(curves[key], grid)
        ax.plot(grid, m, color=COLORS[name], label=_d(name), lw=1.2)
        ax.fill_between(grid, lo, hi, color=COLORS[name], alpha=0.15,
                        lw=0)
    ax.axhline(REF_P, ls=":", c="k", lw=0.9)
    ax.set_xlim(0, xmax)
    ax.set_xlabel("探测次数 $N_\\mathrm{call}$")
    ax.set_ylabel("目标存在域占比估计 $\\hat{P}_s$")
    ax.text(0.03, 0.95, "(a)", transform=ax.transAxes,
            fontweight="bold", fontsize=10, va="top")
    ax.legend(loc="upper right", ncol=2, framealpha=0.95,
              edgecolor="0.4", borderpad=0.35, labelspacing=0.3)

    ax = axes[1]
    xmax_b = max(max(r["x"][-1] for r in curves[f"{case}|{name}"])
                 for name in axb_methods if f"{case}|{name}" in curves)
    grid_b = np.geomspace(1, xmax_b, 400)
    for name in axb_methods:
        key = f"{case}|{name}"
        if key not in curves:
            continue
        m, lo, hi = _median_band(curves[key], grid_b)
        ax.plot(grid_b, m, color=COLORS[name], label=_d(name), lw=1.2)
        ax.fill_between(grid_b, lo, hi, color=COLORS[name], alpha=0.15,
                        lw=0)
    ax.axhline(REF_P, ls=":", c="k", lw=0.9, label="参考值")
    ax.set_xscale("log")
    ax.set_xlabel("探测次数 $N_\\mathrm{call}$")
    ax.text(0.03, 0.95, "(b)", transform=ax.transAxes,
            fontweight="bold", fontsize=10, va="top")
    ax.legend(loc="lower right", framealpha=0.95, edgecolor="0.4",
              borderpad=0.35)
    save(fig, fname)
    rows = []
    for name in METHOD_ORDER:
        for ri, run in enumerate(curves.get(f"{case}|{name}", [])):
            for x, y in zip(run["x"], run["y"]):
                rows.append({"method": name, "rep": ri, "calls": x,
                             "p_est": y})
    pd.DataFrame(rows).to_csv(
        os.path.join(CSV, fname + ".csv"), index=False)


def fig_ablation(df):
    """Grouped bars: median + MAD whiskers of calls / rel_err / IoU for
    the ablation configs in both cases. Reps hitting the iteration cap
    (non-converged) are marked with a black triangle."""
    names = ABL_ORDER
    cases = ["静态目标", "机动目标"]
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.6),
                             constrained_layout=True)
    metrics = [("calls", "(a)", "%.0f"),
               ("rel_err", "(b)", "%.1f"),
               ("iou", "(c)", "%.2f")]
    ylabels = {"calls": "探测调用次数", "rel_err": "相对误差/%",
               "iou": "边界 IoU"}
    x = np.arange(len(names))
    w = 0.36
    cap_handle = None
    for ai, (ax, (col, title, fmt)) in enumerate(zip(axes, metrics)):
        ax.set_ylabel(ylabels[col])
        for i, case in enumerate(cases):
            d = df[(df.case == case)]
            med, err = [], []
            for n in names:
                m, mad = figstyle.med_mad(
                    d[d.method == n][col].to_numpy())
                med.append(m)
                err.append(mad)
            xpos = x + (i - 0.5) * w
            bars = ax.bar(xpos, med, w, yerr=err, capsize=2,
                          color=CASE_COLORS[case], label=case,
                          error_kw=dict(lw=0.8, capthick=0.8))
            for b, v, e, n in zip(bars, med, err, names):
                if col == "calls":  # 数值标签仅保留在 (a)，其余见表5
                    ax.text(b.get_x() + b.get_width() / 2, v + e,
                            fmt % v, ha="center", va="bottom",
                            fontsize=6.2)
                if col == "calls":
                    nc = figstyle.n_nonconv(d[d.method == n], case)
                    if nc:
                        cap_handle = ax.scatter(
                            b.get_x() + b.get_width() / 2,
                            v + e + 24, marker="^", s=12, c="k",
                            zorder=5)
                        ax.text(b.get_x() + b.get_width() / 2,
                                v + e + 30, f"$\\times${nc}",
                                ha="center", va="bottom", fontsize=5.5)
        ax.set_xticks(x)
        ax.set_xticklabels([_d(n) for n in names], rotation=22,
                           ha="right", rotation_mode="anchor")
        ax.set_title(title, loc="left", fontweight="bold", fontsize=10,
                     pad=3)
        ax.tick_params(axis="x", labelsize=7)
    for ax, (col, title, fmt) in zip(axes, metrics):
        vals = []
        for case in cases:
            d = df[(df.case == case)]
            for n in names:
                m, mad = figstyle.med_mad(
                    d[d.method == n][col].to_numpy())
                vals.append(m + mad)
        ax.set_ylim(0, max(vals) * 1.22)
    handles, labels = axes[0].get_legend_handles_labels()
    if cap_handle is not None:
        handles.append(cap_handle)
        labels.append("达迭代上限（未收敛）")
    axes[0].legend(handles, labels, loc="upper right", framealpha=0.95,
                   edgecolor="0.7", borderpad=0.3)
    save(fig, "fig_ablation")
    rows = []
    for case in cases:
        for n in names:
            d = df[(df.case == case) & (df.method == n)]
            cm, c1, c3 = figstyle.med_iqr(d.calls)
            rows.append({"case": case, "method": n,
                         "calls_median": cm, "calls_q1": c1, "calls_q3": c3,
                         "calls_mean": d.calls.mean(),
                         "calls_std": d.calls.std(),
                         "nonconv": figstyle.n_nonconv(d, case),
                         "err_mean": d.rel_err.mean(),
                         "iou_mean": d.iou.mean()})
    pd.DataFrame(rows).to_csv(os.path.join(CSV, "fig_ablation.csv"),
                              index=False)


def fig_moving_map(grid, maps):
    """Predicted vs true boundary for the moving-target case."""
    n = int(np.sqrt(len(grid)))
    xs = grid[:, 0].reshape(n, n)
    ys = grid[:, 1].reshape(n, n)
    # true field at end of run: drift (0.0025, -0.002)/iter for ~110 iters
    nit = 110
    lobes_end = [{**l, "c": (l["c"][0] + 0.0025 * nit,
                            l["c"][1] - 0.002 * nit)}
                 for l in STATIC_LOBES]
    Gt = field_eval(grid, lobes_end).reshape(n, n)
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.0),
                             constrained_layout=True)
    for ax, key, ttl in [
            (axes[0], "机动目标|本方法", "(a) 启用证据老化剔除"),
            (axes[1], "机动目标|无剔除", "(b) 无证据老化剔除")]:
        m = maps.get(key)
        if m is None:
            continue
        ghat = np.array(m["ghat"]).reshape(n, n)
        doe = np.array(m["doe"])
        cf = ax.contourf(xs, ys, ghat, levels=18, cmap="RdYlBu_r")
        ax.contour(xs, ys, Gt, levels=[0], colors="k", linewidths=1.2)
        ax.contour(xs, ys, ghat, levels=[0], colors="magenta",
                   linestyles="--", linewidths=1.2)
        ax.scatter(doe[:, 0], doe[:, 1], s=3, c="#34495E", alpha=0.35,
                   linewidths=0)
        ax.set_title(ttl)
        ax.set_xlabel("$x$（归一化）")
        ax.set_ylabel("$y$（归一化）")
        ax.set_xlim(0, 1); ax.set_ylim(0, 1)
        ax.set_aspect("equal")
        fig.colorbar(cf, ax=ax, shrink=0.8, pad=0.02).ax.tick_params(
            labelsize=7)
    handles = [plt.Line2D([0], [0], color="k", lw=1.2, label="真实边界"),
               plt.Line2D([0], [0], color="magenta", ls="--", lw=1.2,
                          label="预测边界")]
    axes[1].legend(handles=handles, loc="lower left", framealpha=0.95,
                   edgecolor="0.7", borderpad=0.3)
    save(fig, "fig_moving_map")


def stats_table(df):
    """Detailed stats table → tex fragment: median [Q1,Q3] | mean±std |
    min–max | non-converged | rel_err | IoU."""
    tex = []
    for case in ["静态目标", "机动目标"]:
        tex.append("\\midrule\n\\multicolumn{7}{l}{\\sffamily %s}\\\\"
                   "\n\\midrule" % case)
        for n in METHOD_ORDER:
            d = df[(df.case == case) & (df.method == n)]
            if not len(d):
                continue
            iou = d.iou
            iou_s = ("--" if iou.isna().all()
                     else f"{iou.mean():.3f}$\\pm${iou.std():.3f}")
            m, q1, q3 = figstyle.med_iqr(d.calls)
            nc = figstyle.n_nonconv(d, case)
            nc_s = ("--" if n in ("梳篦覆盖", "随机采样") else str(nc))
            tex.append(
                f"{_d(n)} & {m:.0f} [{q1:.0f}, {q3:.0f}] & "
                f"{d.calls.mean():.0f}$\\pm${d.calls.std():.0f} & "
                f"{d.calls.min():.0f}--{d.calls.max():.0f} & {nc_s} & "
                f"{d.rel_err.mean():.1f} & {iou_s}\\\\")
    tex.append("\\midrule")
    out = "\n".join(tex)
    open(os.path.join(HERE, "..", "latex", "stats_table.tex"), "w")\
        .write(out)
    print("wrote stats_table.tex")
    return out


def main():
    df, curves, maps, grid, sc = load()
    fig_sampling_map(grid, maps, sc)
    _convergence_panels(curves, "静态目标", "fig_convergence_static",
                        ["梳篦覆盖", "随机采样"])
    _convergence_panels(curves, "机动目标", "fig_convergence_moving",
                        ["梳篦覆盖", "随机采样"])
    fig_ablation(df)
    fig_moving_map(grid, maps)
    stats_table(df)
    summ = (df.groupby(["case", "method"])
              .agg(calls_m=("calls", "mean"), calls_s=("calls", "std"),
                   p_m=("p_est", "mean"), err_m=("rel_err", "mean"),
                   err_s=("rel_err", "std"), iou_m=("iou", "mean"),
                   iou_s=("iou", "std"))
              .round(4).reset_index())
    summ.to_csv(os.path.join(RES, "summary.csv"), index=False)
    print(summ.to_string())


if __name__ == "__main__":
    main()
