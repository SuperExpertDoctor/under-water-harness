"""Generate latex/results_macros.tex from experiments/results/metrics.csv."""
import pandas as pd, os

RES = "results"
df = pd.read_csv(os.path.join(RES, "metrics.csv"))


def agg(case, name, col):
    s = df[(df.case == case) & (df.method == name)][col]
    return s.mean(), s.std()


def med(case, name, col):
    """Median of a long-tailed metric — used for call-count claims in
    prose (std mean is inflated by cap-hitting outliers)."""
    return df[(df.case == case) & (df.method == name)][col].median()


def fnum(v, d=0):
    return f"{v:.{d}f}"


def fpm(case, name, col, d=0):
    m, s = agg(case, name, col)
    return f"{m:.{d}f}$\\pm${s:.{d}f}"


M = {}
# static
M["callSfull"] = fpm("静态目标", "本方法", "calls")
M["callLfull"] = fpm("静态目标", "梳篦覆盖", "calls")
M["callRfull"] = fpm("静态目标", "随机采样", "calls")
callS = med("静态目标", "本方法", "calls")
callL = med("静态目标", "梳篦覆盖", "calls")
callR = med("静态目标", "随机采样", "calls")
M["callS"], M["callL"], M["callR"] = fnum(callS), fnum(callL), fnum(callR)
M["gainS"] = fnum((callL - callS) / callL * 100, 1)
M["ratio"] = fnum(callS / callL * 100, 1)
for tag, nm in [("NP", "无先验"), ("NI", "无意图"), ("NE", "无剔除")]:
    c = med("静态目标", nm, "calls")
    M[f"call{tag}"] = fnum(c)
    M[f"pest{tag}"] = fnum(agg("静态目标", nm, "p_est")[0], 4)
    M[f"err{tag}"] = fnum(agg("静态目标", nm, "rel_err")[0], 1)
    M[f"call{tag}full"] = fpm("静态目标", nm, "calls")
for tag, nm in [("L", "梳篦覆盖"), ("R", "随机采样")]:
    M[f"pest{tag}"] = fnum(agg("静态目标", nm, "p_est")[0], 4)
    M[f"err{tag}"] = fnum(agg("静态目标", nm, "rel_err")[0], 1)
M["pestS"] = fnum(agg("静态目标", "本方法", "p_est")[0], 4)
M["errSfull"] = fpm("静态目标", "本方法", "rel_err", 1)
M["errS"] = fnum(agg("静态目标", "本方法", "rel_err")[0], 1)
M["iouS"] = fnum(agg("静态目标", "本方法", "iou")[0], 3)
# moving
M["callM"] = fnum(med("机动目标", "本方法", "calls"))
M["callLM"] = fnum(med("机动目标", "梳篦覆盖", "calls"))
M["callRM"] = fnum(med("机动目标", "随机采样", "calls"))
M["callMfull"] = fpm("机动目标", "本方法", "calls")
M["pestM"] = fnum(agg("机动目标", "本方法", "p_est")[0], 4)
M["errMfull"] = fpm("机动目标", "本方法", "rel_err", 1)
M["iouM"] = fnum(agg("机动目标", "本方法", "iou")[0], 3)
for tag, nm in [("NPm", "无先验"), ("NIm", "无意图"), ("NEm", "无剔除")]:
    M[f"call{tag}full"] = fpm("机动目标", nm, "calls")
    M[f"pest{tag}"] = fnum(agg("机动目标", nm, "p_est")[0], 4)
    M[f"err{tag}"] = fnum(agg("机动目标", nm, "rel_err")[0], 1)
    M[f"iou{tag}"] = fnum(agg("机动目标", nm, "iou")[0], 3)
    M[f"call{tag}"] = fnum(med("机动目标", nm, "calls"))
M["errM"] = fnum(agg("机动目标", "本方法", "rel_err")[0], 1)
M["errLm"] = fnum(agg("机动目标", "梳篦覆盖", "rel_err")[0], 1)
M["errRm"] = fnum(agg("机动目标", "随机采样", "rel_err")[0], 1)
M["pestLm"] = fnum(agg("机动目标", "梳篦覆盖", "p_est")[0], 4)
M["pestRm"] = fnum(agg("机动目标", "随机采样", "p_est")[0], 4)
# (moving errNE macro intentionally omitted — \errNE in tex refers to the static value)
# ablation deltas (median-based: calls is long-tailed)
callNP = med("静态目标", "无先验", "calls")
M["pctNP"] = fnum((callNP - callS) / callS * 100, 0)
callNI = med("静态目标", "无意图", "calls")
M["difNI"] = f"{(callNI-callS)/callS*100:+.0f}\\%"
errM = agg("机动目标", "本方法", "rel_err")[0]
errNE = agg("机动目标", "无剔除", "rel_err")[0]
M["pctErrM"] = fnum((errNE - errM) / max(errNE, 1e-9) * 100, 0)
M["pctErrMi"] = fnum(errNE - errM, 1)  # absolute pp
iouM = agg("机动目标", "本方法", "iou")[0]
iouNE = agg("机动目标", "无剔除", "iou")[0]
iouNIm = agg("机动目标", "无意图", "iou")[0]
iouNPm = agg("机动目标", "无先验", "iou")[0]
callNEm = med("机动目标", "无剔除", "calls")
callNIm = med("机动目标", "无意图", "calls")
callNPm = med("机动目标", "无先验", "calls")
callM = med("机动目标", "本方法", "calls")
M["pctNIm"] = fnum((callNIm - callM) / callM * 100, 0)
M["pctNPm"] = fnum((callNPm - callM) / callM * 100, 0)
M["pctNEm"] = fnum((callNEm - callM) / callM * 100, 0)
# new ablation configs: 单侧采样 (SS) / 双无 (NN)
for tag, nm in [("SS", "单侧采样"), ("NN", "双无"), ("IG", "信息增益")]:
    M[f"call{tag}full"] = fpm("静态目标", nm, "calls")
    M[f"pest{tag}"] = fnum(agg("静态目标", nm, "p_est")[0], 4)
    M[f"err{tag}"] = fnum(agg("静态目标", nm, "rel_err")[0], 1)
    M[f"iou{tag}"] = fnum(agg("静态目标", nm, "iou")[0], 3)
    M[f"call{tag}"] = fnum(med("静态目标", nm, "calls"))
for tag, nm in [("SSm", "单侧采样"), ("NNm", "双无"), ("IGm", "信息增益")]:
    M[f"call{tag}full"] = fpm("机动目标", nm, "calls")
    M[f"pest{tag}"] = fnum(agg("机动目标", nm, "p_est")[0], 4)
    M[f"err{tag}"] = fnum(agg("机动目标", nm, "rel_err")[0], 1)
    M[f"iou{tag}"] = fnum(agg("机动目标", nm, "iou")[0], 3)
    M[f"call{tag}"] = fnum(med("机动目标", nm, "calls"))
M["pctNNm"] = fnum((med("机动目标", "双无", "calls") - callM) / callM * 100, 0)
M["pctNNs"] = fnum((med("静态目标", "双无", "calls") - callS) / callS * 100, 0)
# reference true value
M["refS"] = fnum(df["ref"].iloc[0], 4)

lines = [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in M.items()]
hdr = "% Auto-generated from experiments/results/metrics.csv — do not edit.\n"
open("../latex/results_macros.tex", "w").write(hdr + "\n".join(lines) + "\n")
print(f"{len(M)} macros written")
for k in ["callSfull", "callLfull", "callRfull", "callMfull", "pctNP",
          "difNI", "errM", "errNE", "pctErrM", "iouS", "iouM", "iouNE",
          "iouNIm", "iouNPm", "pctNIm", "pctNPm", "pctNEm"]:
    print(f"  {k} = {M.get(k)}")
