"""解释某一本为什么被推荐。

默认用本地证据模板，不需要任何 API。用户在设置里配置了 OpenAI 兼容接口后，
“深入分析”会把这一本的候选信息、匹配证据和最接近的几本收藏（只有标题、作者、标签）
发给模型做比较和总结；账号凭据永远不会发送。
"""
from __future__ import annotations

import requests

from .config import load_settings, proxies
from .db import DB
from .features import FEATURES
from .ranker import BASE_WEIGHTS, SEMANTIC_WEIGHTS

FEATURE_NAMES = {"tag": "标签匹配", "fit": "口味契合", "author": "作者亲和", "title": "标题相似", "semantic": "语义相似",
                 "neg": "像你略过的作品", "pop": "站内热度"}


def evidence(db: DB, batch_id: int, pos: int) -> dict | None:
    items = db.batch_items(batch_id)
    it = next((x for x in items if x["pos"] == pos), None)
    if not it:
        return None
    w = db.get_work(it["site"], it["wid"])
    meta = db.batch_meta(batch_id) or {}
    nearest = []
    for n in it["reason"].get("nearest", []):
        f = db.get_work(n["site"], n["wid"])
        if f:
            nearest.append({"title": f.title, "authors": f.authors, "tags": f.tags[:12],
                            "parodies": f.parodies, "site": "禁漫" if f.site == "jm" else "哔咔",
                            "similarity": n.get("sim")})
    return {
        "batch_id": batch_id, "pos": pos, "engine": meta.get("engine"),
        "candidate": {"title": w.title if w else it["wid"], "authors": w.authors if w else [],
                      "tags": w.tags if w else [], "parodies": w.parodies if w else [],
                      "description": (w.description[:300] if w else ""), "url": w.url if w else ""},
        "route": it["reason"].get("route_name", it["route"]),
        "features": it["features"], "score": it["score"],
        "reason": it["reason"].get("text", ""),
        "shared_tags": it["reason"].get("shared_tags", []),
        "author": it["reason"].get("author", ""),
        "nearest_favorites": nearest,
    }


def local_explanation(ev: dict) -> str:
    sem_on = "semantic" in (ev.get("engine") or "")
    weights = SEMANTIC_WEIGHTS if sem_on else BASE_WEIGHTS
    contrib = sorted(
        ((k, weights[k] * (ev["features"].get(k, 0.0) - (0.5 if k == "fit" else 0.0))) for k in FEATURES if weights[k]),
        key=lambda kv: -abs(kv[1]),
    )
    lines = [f"Vol.{ev['batch_id']} · No.{ev['pos']}　{ev['candidate']['title']}",
             f"来路：{ev['route']}。{ev['reason']}"]
    top = [f"{FEATURE_NAMES[k]} {v:+.2f}" for k, v in contrib[:4] if abs(v) >= 0.01]
    if top:
        lines.append("分数从哪来（固定权重）：" + "，".join(top))
    if ev["nearest_favorites"]:
        lines.append("书架上离它最近的几本：")
        for n in ev["nearest_favorites"]:
            lines.append(f"  · [{n['site']}] {n['title']}（作者 {'、'.join(n['authors']) or '未知'}）")
    lines.append("（依据只来自标题、作者和标签这些文字，画风合不合，还得你自己翻一眼。）")
    return "\n".join(lines)


def llm_configured(settings: dict | None = None) -> bool:
    s = (settings or load_settings()).get("llm") or {}
    return bool(s.get("base_url") and s.get("api_key") and s.get("model"))


def llm_explanation(ev: dict, question: str = "") -> str:
    settings = load_settings()
    cfg = settings.get("llm") or {}
    if not llm_configured(settings):
        raise RuntimeError("没有配置大模型接口，可在设置里填写（OpenAI 兼容格式）")
    import json
    prompt = (
        "你是漫画推荐助手。下面是本地推荐系统给出的一本推荐作品，以及它的匹配证据和用户最接近的几本收藏。"
        "请只根据这些文字证据，用简体中文比较这本与那几本收藏在题材、角色、作者上的共同点和差异，"
        "说明用户可能喜欢或不喜欢它的理由。资料只有文字，不要断言画风相同。控制在 200 字内。\n\n"
        + json.dumps(ev, ensure_ascii=False, indent=1)
        + (f"\n\n用户追问：{question}" if question else "")
    )
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    r = requests.post(url, headers={"Authorization": f"Bearer {cfg['api_key']}"},
                      json={"model": cfg["model"], "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.3},
                      timeout=60, proxies=proxies(settings))
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()
