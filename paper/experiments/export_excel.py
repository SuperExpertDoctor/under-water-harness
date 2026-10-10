"""Export all experiment data to Excel (journal requirement: figures with
Excel data)."""
import json, os
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "results")
OUT = os.path.join(HERE, "figures", "data", "experiment_data.xlsx")

df = pd.read_csv(os.path.join(RES, "metrics.csv"))
curves = json.load(open(os.path.join(RES, "curves.json")))

summary = (df.groupby(["case", "method"])
           .agg(calls_mean=("calls", "mean"), calls_std=("calls", "std"),
                calls_median=("calls", "median"), calls_min=("calls", "min"),
                calls_max=("calls", "max"), p_mean=("p_est", "mean"),
                err_mean=("rel_err", "mean"), err_std=("rel_err", "std"),
                iou_mean=("iou", "mean"), iou_std=("iou", "std"))
           .round(4).reset_index())

crows = []
for key, runs in curves.items():
    case, method = key.split("|")
    for ri, run in enumerate(runs):
        for x, y in zip(run["x"], run["y"]):
            crows.append({"case": case, "method": method, "rep": ri,
                          "calls": x, "p_est": y})
cdf = pd.DataFrame(crows)

with pd.ExcelWriter(OUT, engine="openpyxl") as w:
    df.to_excel(w, sheet_name="逐次原始数据", index=False)
    summary.to_excel(w, sheet_name="统计汇总", index=False)
    cdf.to_excel(w, sheet_name="收敛曲线数据", index=False)
    for name, sheet in [("delta", "Δ敏感性"), ("prior", "先验鲁棒性"),
                        ("scale", "规模标度"), ("noise", "观测噪声"),
                        ("llm", "LLM编译统计")]:
        for fname in ("metrics.csv", "llm_stats.csv"):
            p = os.path.join(HERE, "results_sweep", name, fname)
            if os.path.exists(p):
                pd.read_csv(p).to_excel(w, sheet_name=sheet, index=False)
                break
    wp = os.path.join(RES, "wilcoxon.csv")
    if os.path.exists(wp):
        pd.read_csv(wp).to_excel(w, sheet_name="Wilcoxon检验", index=False)
    up = os.path.join(RES, "llm_updates.json")
    if os.path.exists(up):
        urows = []
        for u in json.load(open(up)):
            c = u.get("clamped", {})
            urows.append({"case": u["case"], "method": u["method"],
                          "rep": u["rep"], "iter": u["iter"],
                          "situation": u.get("situation", ""),
                          **{f"clamped_{k}": v for k, v in c.items()}})
        if urows:
            pd.DataFrame(urows).to_excel(w, sheet_name="闭环重编译日志",
                                         index=False)
print("wrote", OUT)
