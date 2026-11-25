"""固定打分基线 + 个人排序器。

个人排序器只从明确的“心动”(1) 和“略过”(0) 学习，输入是当时展示时的特征。
它不是从零学起，而是以固定权重为起点做微调（贝叶斯逻辑回归，先验中心就是固定权重）：
反馈少时几乎等于固定打分，反馈越多越贴近你，不会因为几条反馈就乱跳。
  - 心动、略过各不到 3 条：只用固定打分
  - 够了：用微调后的权重排序；若最近几卷的检验明显不如固定打分，退回固定打分
标签层面的偏好（你对哪些标签心动）在 taste.py 里学，直接体现在“口味契合”这一项。
"""
from __future__ import annotations

import math
import time

import numpy as np

from .db import DB
from .features import FEATURES
from .normalize import norm_person

BASE_WEIGHTS = {"tag": 0.30, "fit": 0.20, "author": 0.30, "title": 0.12, "semantic": 0.0, "neg": -0.35,
                "pop": 0.08}
SEMANTIC_WEIGHTS = {"tag": 0.22, "fit": 0.18, "author": 0.25, "title": 0.07, "semantic": 0.20, "neg": -0.35,
                    "pop": 0.08}
# 旧版权重（没有口味契合），只用于对比评测
V1_WEIGHTS = {"tag": 0.40, "fit": 0.0, "author": 0.30, "title": 0.15, "semantic": 0.0, "neg": -0.35, "pop": 0.10}

MIN_EACH = 3         # 心动、略过各至少几条才启用个人排序器
LOSS_MARGIN = 0.05   # 最近几卷上比固定打分差这么多（AUC）就退回
SCALE = 6.0          # 固定打分（约 0~1）换算成对数几率的倍数
PRIOR_SD = 0.7       # 各项权重偏离固定权重的先验标准差


def base_score(f: dict, semantic_on: bool, weights: dict | None = None) -> float:
    w = weights or (SEMANTIC_WEIGHTS if semantic_on else BASE_WEIGHTS)
    return sum(w.get(k, 0.0) * f.get(k, 0.0) for k in FEATURES)


def fit_map(X: np.ndarray, y: np.ndarray, base: np.ndarray) -> dict:
    """以固定权重为先验中心的逻辑回归。
    对数几率 = b + g·SCALE·base + d·x；g 的先验中心是 1，d 的先验中心是 0。"""
    from scipy.optimize import minimize
    n, k = X.shape
    pos = max(1, int(y.sum()))
    neg = max(1, n - pos)
    sw = np.where(y == 1, n / (2 * pos), n / (2 * neg))   # 两类等权

    def nlp(theta):
        b, g, d = theta[0], theta[1], theta[2:]
        z = b + g * SCALE * base + X @ d
        p = 1 / (1 + np.exp(-z))
        ll = np.sum(sw * (y * np.logaddexp(0, -z) + (1 - y) * np.logaddexp(0, z)))
        prior = b ** 2 / (2 * 4.0) + (g - 1) ** 2 / (2 * 0.25) + np.sum(d ** 2) / (2 * PRIOR_SD ** 2)
        r = p - y
        grad_b = np.sum(sw * r) + b / 4.0
        grad_g = np.sum(sw * r * SCALE * base) + (g - 1) / 0.25
        grad_d = X.T @ (sw * r) + d / PRIOR_SD ** 2
        return ll + prior, np.concatenate([[grad_b, grad_g], grad_d])

    theta0 = np.concatenate([[0.0, 1.0], np.zeros(k)])
    bounds = [(None, None), (0.0, None)] + [(None, None)] * k
    res = minimize(nlp, theta0, jac=True, method="L-BFGS-B", bounds=bounds)
    t = res.x
    return {"bias": float(t[0]), "gain": float(t[1]), "delta": {f: float(v) for f, v in zip(FEATURES, t[2:])}}


def _vec(f: dict) -> list[float]:
    return [float(f.get(k, 0.0)) for k in FEATURES]


def _auc(y: np.ndarray, s: np.ndarray) -> float | None:
    pos, neg = s[y == 1], s[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return float(wins / (len(pos) * len(neg)))


def training_rows(db: DB) -> list[dict]:
    """取有明确反馈、且反馈对应那次展示的条目（用展示时的特征）。"""
    rows = db.q(
        "SELECT i.batch_id, i.features, i.site, i.wid, f.label FROM feedback f "
        "JOIN batch_items i ON i.site=f.site AND i.wid=f.wid AND i.batch_id=f.batch_id "
        "WHERE f.label IN ('want', 'seen_like', 'skip', 'dislike') ORDER BY i.batch_id"
    )
    import json
    out = []
    for r in rows:
        w = db.get_work(r["site"], r["wid"])
        authors = {norm_person(a) for a in (w.authors if w else [])} or {r["wid"]}
        out.append({"batch_id": r["batch_id"], "x": _vec(json.loads(r["features"])),
                    "y": 1 if r["label"] in ("want", "seen_like") else 0, "authors": authors})
    # 调音里给推荐作品的评分也算：本命/喜欢 = 心动，不太喜欢 = 略过（已有心动/略过的不重复算）
    seen = {(r["site"], r["wid"]) for r in rows}
    cal = db.q(
        "SELECT c.site, c.wid, c.level, MAX(i.batch_id) batch_id, i.features FROM calibration c "
        "JOIN batch_items i ON i.site=c.site AND i.wid=c.wid "
        "WHERE c.source='rec' AND c.level IN (1, 3, 4) GROUP BY c.site, c.wid"
    )
    for r in cal:
        if (r["site"], r["wid"]) in seen:
            continue
        w = db.get_work(r["site"], r["wid"])
        authors = {norm_person(a) for a in (w.authors if w else [])} or {r["wid"]}
        out.append({"batch_id": r["batch_id"], "x": _vec(json.loads(r["features"])),
                    "y": 1 if r["level"] >= 3 else 0, "authors": authors})
    out.sort(key=lambda r: r["batch_id"])
    return out


def train(db: DB, semantic_on: bool) -> dict:
    """重新评估个人排序器，结果存到 kv['ranker']。"""
    rows = training_rows(db)
    n_pos = sum(r["y"] for r in rows)
    n_neg = len(rows) - n_pos
    authors = set().union(*[r["authors"] for r in rows]) if rows else set()
    batches = sorted({r["batch_id"] for r in rows})
    state = {"mode": "baseline", "alpha": 0.0, "n_pos": n_pos, "n_neg": n_neg,
             "n_authors": len(authors), "n_batches": len(batches), "updated_at": time.time(),
             "weights": None, "auc_base": None, "auc_model": None, "semantic": semantic_on}

    if n_pos == 0 or n_neg == 0:
        state["message"] = "还没同时收到“心动”和“略过”，先按固定打分排。"
        db.kv_set("ranker", state)
        return state
    if n_pos < MIN_EACH or n_neg < MIN_EACH:
        state["message"] = (f"已有心动 {n_pos}、略过 {n_neg}，各满 {MIN_EACH} 条后开始按你的反馈微调；"
                            "你点过的标签已经在影响“口味契合”。")
        db.kv_set("ranker", state)
        return state

    X = np.array([r["x"] for r in rows])
    y = np.array([r["y"] for r in rows], dtype=float)
    base = np.array([base_score(dict(zip(FEATURES, x)), semantic_on) for x in X])

    # 检验：较早批次微调、最近约三分之一的批次测试（批次够多时）
    if len(batches) >= 3:
        cut = batches[max(1, int(len(batches) * 2 / 3)) - 1]
        tr = np.array([r["batch_id"] <= cut for r in rows])
        te = ~tr
        if len(set(y[tr])) == 2 and len(set(y[te])) == 2:
            m = fit_map(X[tr], y[tr], base[tr])
            ms = m["gain"] * SCALE * base[te] + X[te] @ np.array([m["delta"][f] for f in FEATURES])
            state["auc_model"], state["auc_base"] = _auc(y[te], ms), _auc(y[te], base[te])

    m = fit_map(X, y, base)
    state["weights"] = {k: round(float(m["gain"] * SCALE * (SEMANTIC_WEIGHTS if semantic_on else BASE_WEIGHTS)[k]
                                       + m["delta"][k]), 4) for k in FEATURES}
    state["bias"] = round(m["bias"], 4)
    auc_m, auc_b = state["auc_model"], state["auc_base"]
    if auc_m is not None and auc_b is not None and auc_m < auc_b - LOSS_MARGIN:
        state["mode"] = "shadow"
        state["message"] = (f"按你反馈微调的权重在最近几卷上不如固定打分（AUC {auc_m:.2f} 对 {auc_b:.2f}），"
                            "暂时退回固定打分，继续观察。")
    else:
        state["mode"] = "personal"
        state["alpha"] = 1.0
        extra = f"，最近几卷检验 AUC {auc_m:.2f}（固定打分 {auc_b:.2f}）" if auc_m is not None else ""
        state["message"] = f"已按你的 {n_pos} 条心动、{n_neg} 条略过微调排序{extra}。"
    db.kv_set("ranker", state)
    return state


def model_score(state: dict, f: dict) -> float | None:
    w = state.get("weights")
    if not w:
        return None
    return state.get("bias", 0.0) + sum(w.get(k, 0.0) * f.get(k, 0.0) for k in FEATURES)


def _pct_rank(vals: list[float]) -> list[float]:
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    for r, i in enumerate(order):
        out[i] = r / max(1, len(vals) - 1)
    return out


def final_scores(feats: list[dict], semantic_on: bool, state: dict | None) -> tuple[list[float], str]:
    base = [base_score(f, semantic_on) for f in feats]
    engine = "baseline+semantic" if semantic_on else "baseline"
    if not state or state.get("mode") != "personal" or not state.get("weights"):
        return base, engine
    ms = [model_score(state, f) or 0.0 for f in feats]
    # 换回 0~1 左右的量纲，方便和固定打分比较、展示
    return [1 / (1 + math.exp(-m)) for m in ms], engine + "+personal"


def describe_weights(state: dict) -> str:
    w = state.get("weights")
    if not w:
        return ""
    names = {"tag": "标签", "fit": "口味契合", "author": "作者", "title": "标题", "semantic": "语义", "neg": "负例",
             "pop": "热度"}
    parts = sorted(w.items(), key=lambda kv: -abs(kv[1]))
    return "，".join(f"{names[k]} {v:+.2f}" for k, v in parts if not math.isclose(v, 0.0))
