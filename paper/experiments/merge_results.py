"""Merge shard result dirs (results_a/b/c) into results/ for figures+macros."""
import json, os, shutil
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SHARDS = ["results_a", "results_b", "results_c"]
OUT = os.path.join(HERE, "results")
os.makedirs(OUT, exist_ok=True)

dfs, curves, maps = [], {}, {}
for s in SHARDS:
    d = os.path.join(HERE, s)
    if not os.path.exists(os.path.join(d, "metrics.csv")):
        continue
    dfs.append(pd.read_csv(os.path.join(d, "metrics.csv")))
    for k, v in json.load(open(os.path.join(d, "curves.json"))).items():
        curves.setdefault(k, []).extend(v)
    maps.update(json.load(open(os.path.join(d, "maps.json"))))

df = pd.concat(dfs, ignore_index=True)
df.to_csv(os.path.join(OUT, "metrics.csv"), index=False)
json.dump(curves, open(os.path.join(OUT, "curves.json"), "w"))
json.dump(maps, open(os.path.join(OUT, "maps.json"), "w"))
shutil.copy(os.path.join(HERE, SHARDS[0], "grid.npy"),
            os.path.join(OUT, "grid.npy"))
shutil.copy(os.path.join(HERE, SHARDS[0], "scenario.json"),
            os.path.join(OUT, "scenario.json"))
print(f"merged {len(dfs)} shards, {len(df)} rows")
print(df.groupby(["case", "method"])[["calls", "rel_err", "iou"]]
      .agg(["mean", "std"]).round(3).to_string())
