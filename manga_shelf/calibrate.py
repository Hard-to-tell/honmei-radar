"""口味调音：每轮挑 5 本让你打分，给收藏分层、给口味簇定权重。

第 1 轮：按簇占比从大到小，每簇挑 1 本最有代表性的（离簇中心近、且是较新的收藏）。
第 2 轮起：挑“拿不准”的：评分好坏混杂的簇 > 还没评过的簇 > 同时像两个簇的作品。
第 3 轮起：5 本里 1 本来自最近的推荐，1 本是站内随机作品，其余仍从收藏挑。
已评过的不再出现；“跳过”的一周后可以再出现。
"""
from __future__ import annotations

import random
import time
from collections import Counter, defaultdict

import numpy as np

from .db import DB
from .models import Work
from .normalize import parse_title
from .taste import CALIB_VALUE, LEVEL_NAMES, background_works

ROUND_SIZE = 5
SKIP_COOLDOWN = 7 * 24 * 3600
SOURCE_NAMES = {"fav": "你的收藏", "rec": "最近的推荐", "random": "站内随手翻到"}


def current_round(db: DB) -> int:
    r = db.q("SELECT MAX(round) r FROM calibration WHERE level > 0")
    return int(r[0]["r"] or 0) + 1


def _blocked(db: DB) -> set[tuple[str, str]]:
    now = time.time()
    return {k for k, c in db.calibration_map().items() if c["level"] > 0 or now - c["at"] < SKIP_COOLDOWN}


def _card(w: Work, source: str, cluster: str = "") -> dict:
    return {"site": w.site, "wid": w.wid, "title": parse_title(w.title).core or w.title, "full_title": w.title,
            "authors": w.authors[:2], "tags": w.tags[:8], "cover": f"/api/cover/{w.site}/{w.wid}",
            "url": w.url, "source": source, "source_name": SOURCE_NAMES[source], "cluster": cluster,
            "site_name": "禁漫" if w.site == "jm" else "哔咔"}


def pick_round(db: DB, seed: int | None = None) -> dict:
    from .recommend import build_profile, build_taste
    rnd = random.Random(seed if seed is not None else time.time())
    rno = current_round(db)
    profile = build_profile(db)
    if profile.empty:
        return {"round": rno, "items": [], "message": "先导入收藏，再来调音。"}
    taste = build_taste(db, profile)
    blocked = _blocked(db)
    favs = profile.favorites
    newness = profile.fav_weights
    cards: list[dict] = []
    used: set[tuple[str, str]] = set()

    def add(i: int) -> None:
        w = favs[i]
        if w.key in blocked or w.key in used:
            return
        used.add(w.key)
        name = ""
        if taste.clusters:
            for c in taste.clusters:
                if i in c["members"]:
                    name = c["name"]
        cards.append(_card(w, "fav", name))

    n_fav = ROUND_SIZE - (2 if rno >= 3 else 0)
    if not taste.clusters:
        for i in np.argsort(-newness):
            if len(cards) >= n_fav:
                break
            add(int(i))
    else:
        dist = taste.cluster_distances(favs)
        member_of = {i: ci for ci, c in enumerate(taste.clusters) for i in c["members"]}
        if rno == 1:
            # 每簇一本代表作；簇不够 5 个时，从大簇里再补
            passes = 0
            while len(cards) < n_fav and passes < 3:
                for ci, c in enumerate(taste.clusters):
                    if len(cards) >= n_fav:
                        break
                    mem = [i for i in c["members"] if favs[i].key not in blocked and favs[i].key not in used]
                    if not mem:
                        continue
                    best = min(mem, key=lambda i: dist[i, ci] - 0.15 * newness[i])
                    add(best)
                passes += 1
        else:
            def priority(i: int) -> float:
                ci = member_of.get(i, -1)
                c = taste.clusters[ci] if ci >= 0 else {}
                n = c.get("n_signals", 0)
                mixed = c.get("uncertainty", 1.0) - 1 / np.sqrt(n + 1) if n >= 2 else 0.0
                d = np.sort(dist[i])
                ambiguous = d[0] / max(d[1], 1e-6) if len(d) > 1 else 0.0   # 越接近 1 越像两个簇
                return 3 * mixed + (1.0 if n == 0 else 0.0) + 0.8 * ambiguous + 0.2 * newness[i] + rnd.random() * 0.2
            per_c: Counter = Counter()
            for i in sorted(range(len(favs)), key=lambda i: -priority(i)):
                if len(cards) >= n_fav:
                    break
                ci = member_of.get(i, -1)
                if per_c[ci] >= 2 or favs[i].key in blocked:
                    continue
                before = len(cards)
                add(i)
                if len(cards) > before:
                    per_c[ci] += 1

    if rno >= 3:
        fav_keys = {w.key for w in favs}
        fb = db.feedback_map()
        recent = db.q("SELECT site, wid FROM batch_items WHERE batch_id IN "
                      "(SELECT id FROM batches ORDER BY id DESC LIMIT 3) ORDER BY batch_id DESC, pos")
        for r in recent:
            k = (r["site"], r["wid"])
            w = db.get_work(*k)
            if w and k not in blocked and k not in fav_keys and k not in fb and k not in used:
                used.add(k)
                cards.append(_card(w, "rec", _cluster_name(taste, w)))
                break
        pool = [w for w in background_works(db, ("latest", "random"))
                if w.key not in blocked and w.key not in fav_keys and w.key not in used]
        if pool:
            w = rnd.choice(pool)
            cards.append(_card(w, "random", _cluster_name(taste, w)))
    return {"round": rno, "items": cards[:ROUND_SIZE]}


def _cluster_name(taste, w: Work) -> str:
    if not taste.clusters:
        return ""
    ci = taste.cluster_of([w])[0]
    return taste.clusters[ci]["name"] if ci >= 0 else ""


def rate(db: DB, site: str, wid: str, level: int | None, reasons: list[str] | None, rnd: int, source: str) -> None:
    allowed = {"实用", "艺术", "画风", "题材", "剧情"}
    reasons = [r for r in (reasons or []) if r in allowed]
    if any(r in ("画风", "题材", "剧情") for r in reasons) and "艺术" not in reasons:
        reasons.append("艺术")
    db.set_calibration(site, wid, level, reasons, rnd, source if source in SOURCE_NAMES else "fav")


def round_summary(db: DB, rno: int) -> dict:
    from .recommend import build_profile, build_taste
    cal = {k: c for k, c in db.calibration_map().items() if c["round"] == rno}
    items = []
    for (s, wid), c in cal.items():
        w = db.get_work(s, wid)
        if w:
            items.append({**_card(w, c["source"]), "level": c["level"], "level_name": LEVEL_NAMES[c["level"]],
                          "reasons": c["reasons"]})
    rated = [it for it in items if it["level"] > 0]
    if not rated:
        return {"round": rno, "items": items, "text": "这一轮全跳过了，没关系，下次再调。"}
    profile = build_profile(db)
    taste = build_taste(db, profile)
    by_cluster: dict[str, list[float]] = defaultdict(list)
    for it in rated:
        w = db.get_work(it["site"], it["wid"])
        name = _cluster_name(taste, w) if w else ""
        it["cluster"] = name
        if name:
            by_cluster[name].append(CALIB_VALUE[it["level"]])
    parts = []
    if by_cluster:
        avg = {k: sum(v) / len(v) for k, v in by_cluster.items()}
        best = max(avg, key=avg.get)
        worst = min(avg, key=avg.get)
        if avg[best] >= 0.5:
            parts.append(f"这一轮里，你偏爱「{best}」这一簇")
        if worst != best and avg[worst] <= -0.5:
            parts.append(f"「{worst}」似乎是误入")
    lv = Counter(it["level"] for it in rated)
    if not parts:
        parts.append(f"评了 {len(rated)} 本：本命 {lv[4]}、喜欢 {lv[3]}、一般 {lv[2]}、不太喜欢 {lv[1]}")
    reason_cnt = Counter(r for it in rated for r in it["reasons"])
    if reason_cnt:
        top = reason_cnt.most_common(1)[0][0]
        parts.append(f"你更常因为「{top}」而喜欢")
    muted = [c["name"] for c in taste.clusters if c.get("muted")]
    text = "；".join(parts) + "。"
    if muted:
        text += f"「{'」「'.join(muted)}」之后会很少出现在推荐里。"
    return {"round": rno, "items": items, "text": text,
            "clusters": [{"name": c["name"], "weight": c.get("weight", 1.0), "muted": c.get("muted", False),
                          "share": c["share"]} for c in taste.clusters]}
