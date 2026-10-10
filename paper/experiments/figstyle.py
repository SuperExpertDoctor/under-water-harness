"""Shared SCI plotting style and robust statistics for paper figures.

Presentation convention agreed for this paper: metrics with long right
tails (探测次数) are reported as median + inter-quartile range (IQR);
reps that hit MAX_ITERS without converging are flagged separately
("未收敛") rather than silently inflating the std.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def apply():
    plt.rcParams["font.serif"] = ["Noto Serif CJK SC", "TeX Gyre Termes",
                                 "DejaVu Serif"]
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["mathtext.fontset"] = "dejavuserif"
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["font.size"] = 8
    plt.rcParams["axes.labelsize"] = 8.5
    plt.rcParams["axes.titlesize"] = 8.5
    plt.rcParams["xtick.labelsize"] = 7.5
    plt.rcParams["ytick.labelsize"] = 7.5
    plt.rcParams["legend.fontsize"] = 7.5
    plt.rcParams["xtick.direction"] = "in"
    plt.rcParams["ytick.direction"] = "in"
    plt.rcParams["axes.linewidth"] = 0.8
    plt.rcParams["lines.linewidth"] = 1.2
    plt.rcParams["axes.grid"] = True
    plt.rcParams["grid.linestyle"] = ":"
    plt.rcParams["grid.linewidth"] = 0.5
    plt.rcParams["grid.alpha"] = 0.5
    plt.rcParams["axes.axisbelow"] = True


# Okabe-Ito colorblind-safe palette
COLORS = {"本方法": "#D55E00", "无先验": "#E69F00", "无意图": "#CC79A7",
          "无剔除": "#009E73", "单侧采样": "#56B4E9", "双无": "#0072B2",
          "信息增益": "#F0E442", "梳篦覆盖": "#000000", "随机采样": "#999999",
          "全覆盖扫描": "#000000"}
CASE_COLORS = {"静态目标": "#0072B2", "机动目标": "#D55E00"}

METHOD_ORDER = ["本方法", "无先验", "无意图", "无剔除", "单侧采样", "双无",
                "信息增益", "梳篦覆盖", "随机采样"]
ABL_ORDER = ["本方法", "无先验", "无意图", "无剔除", "单侧采样", "双无"]
CURVE_ORDER = ABL_ORDER + ["信息增益"]
DISP = {"梳篦覆盖": "全覆盖扫描", "双无": "无先验无意图"}


def _d(name):
    return DISP.get(name, name)


# calls at which a run has exhausted MAX_ITERS without the ε-criterion
# being met (n_init + MAX_ITERS*B; B=3 static, B=6 moving)
CAP = {"静态目标": 504, "机动目标": 984}


def med_iqr(s):
    s = np.asarray(s, float)
    return np.median(s), np.percentile(s, 25), np.percentile(s, 75)


def n_nonconv(d, case):
    """Reps that exhausted MAX_ITERS without meeting the eps criterion.
    Uses the runner-reported `converged` flag (batch size varies under
    closed-loop re-compilation, so a calls threshold is not robust)."""
    if "converged" in d.columns:
        return int((d.converged == False).sum())
    return int((d.calls >= CAP[case]).sum())


def iqr_yerr(s):
    """Asymmetric error-bar array for matplotlib: median with Q1/Q3
    whiskers."""
    m, q1, q3 = med_iqr(s)
    return m, np.array([[m - q1], [q3 - m]])
