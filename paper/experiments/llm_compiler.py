"""LLM semantic compiler: text intelligence -> prior map; commander intent ->
bounded acquisition parameters. Real calls to the LongCat OpenAI-compatible
endpoint; raw responses are saved for provenance.
"""
import json
import os
import re
import requests

API = "https://api.longcat.chat/openai/v1/chat/completions"
MODEL = "LongCat-2.0"

PRIOR_SCHEMA = """输出严格 JSON（不要多余文字）：
{"hotspots":[{"mu":[x,y],"sigma":s,"w":w}], "confidence":c}
坐标归一化到 [0,1] 正方形海域（x 向东、y 向北）。sigma 为该热点高斯标准差
(0.02~0.25)，w 为权重(0.1~3)，confidence 为先验可信度(0~1)。热点 1~4 个。"""

INTENT_SCHEMA = """输出严格 JSON（不要多余文字），字段全部限定在范围内：
{"p0": 初始筛选比例(0.2~0.8), "p_min": 最低筛选比例(0.02~0.3),
 "decay": 筛选衰减系数(0.6~0.95), "delta": 边界判定阈值(0.02~0.3),
 "dual_ratio": 确认任务占比(0.3~0.7), "explore_w": 探索权重(0.5~1.5),
 "batch": 每轮探测数(2~8整数)}"""


def _call(messages, api_key, temperature=0.2, timeout=120):
    r = requests.post(API,
                      headers={"Authorization": f"Bearer {api_key}",
                               "Content-Type": "application/json"},
                      json={"model": MODEL, "messages": messages,
                            "temperature": temperature},
                      timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _json_from(text):
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0))


def compile_prior(intel_text, api_key):
    """Intel text -> prior spec {hotspots, confidence}; fall back to None."""
    msgs = [{"role": "system",
             "content": "你是海上目标搜索的情报分析员。把非结构化情报转换成"
                        "目标位置先验热点（高斯混合）。宁可把置信圈画大也不"
                        "要漏掉情报暗示的区域。" + PRIOR_SCHEMA},
            {"role": "user", "content": intel_text}]
    spec = _json_from(_call(msgs, api_key))
    comps = [{"mu": tuple(h["mu"]), "cov": [[h["sigma"] ** 2, 0],
                                            [0, h["sigma"] ** 2]],
              "w": h["w"]} for h in spec["hotspots"]]
    return {"comps": comps, "confidence": spec.get("confidence", 0.5),
            "raw": spec}


def compile_intent(intent_text, situation, api_key):
    """Commander intent + situation stats -> bounded acquisition params."""
    msgs = [{"role": "system",
             "content": "你是搜索任务的策略参数编排器。把指挥员意图和当前态势"
                        "编译成主动学习搜索策略参数。" + INTENT_SCHEMA},
            {"role": "user",
             "content": f"指挥员意图：{intent_text}\n当前态势：{situation}"}]
    return _json_from(_call(msgs, api_key))


def clamp_params(p):
    return {"p0": float(min(0.8, max(0.2, p.get("p0", 0.4)))),
            "p_min": float(min(0.3, max(0.02, p.get("p_min", 0.05)))),
            "decay": float(min(0.95, max(0.6, p.get("decay", 0.8)))),
            "delta": float(min(0.3, max(0.02, p.get("delta", 0.1)))),
            "dual_ratio": float(min(0.7, max(0.3, p.get("dual_ratio", 0.5)))),
            "explore_w": float(min(1.5, max(0.5, p.get("explore_w", 1.0)))),
            "batch": int(min(8, max(2, p.get("batch", 4))))}
