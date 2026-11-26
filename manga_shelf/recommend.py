"""生成一批禁漫推荐。

1. 画像：两站收藏都作为偏好输入（按收藏时间加权）；口味模型算每个标签的偏爱度、把收藏分成几簇
2. 候选分路收集（只从禁漫取）：高频作者、每个口味簇的代表标签、原作、近期收藏的相关作品、少量热门
3. 过滤：已收藏（含跨站同一作品）、已反馈、已展示两次以上没表态的
4. 用列表信息粗排（从哪个标签搜到的也算作已知标签），再为前面的候选补详情，算完整特征
5. 打分（固定权重，或按你的反馈微调过的权重）→ 按口味簇分配名额 → 作者去重 → 小步试探 + 一本热门
6. 存成一个批次：固定次序、当时的特征和依据
"""
from __future__ import annotations

import json
import math
import random
import time
from collections import Counter

import numpy as np

from .config import load_settings
from .db import DB
from dataclasses import replace

from .features import FeatureBuilder, Profile, work_people, work_tokens
from .importer import ensure_details
from .models import Work
from .normalize import dedup_keys, norm, norm_person, norm_tag, norm_tags, parse_title, title_key
from .ranker import final_scores, train
from .semantic import SemanticUnavailable, get_semantic
from .sources import SiteError
from .sources.base import Progress, SiteAdapter

SEARCH_TTL = 24 * 3600

# 候选来源的显示名
ROUTE_NAMES = {"author": "熟悉的笔", "tag": "同频", "parody": "同一世界", "related": "相邻", "explore": "偶遇",
               "cold": "初见"}


def _cached_search(db: DB, adapter: SiteAdapter, key: str, fn) -> list[Work]:
    """检索结果缓存一天，避免反复请求。"""
    hit = db.kv_get(f"search:{key}")
    if hit and time.time() - hit["at"] < SEARCH_TTL:
        works = db.get_works([(adapter.site, wid) for wid in hit["ids"]])
        return [works[(adapter.site, wid)] for wid in hit["ids"] if (adapter.site, wid) in works]
    try:
        res = fn()
    except SiteError as e:
        import logging
        logging.getLogger("honmei").warning("检索失败 %s：%s", key, e)
        return []
    for w in res:
        db.upsert_work(w)
    db.kv_set(f"search:{key}", {"at": time.time(), "ids": [w.wid for w in res]})
    return res


def build_profile(db: DB) -> Profile:
    fb = db.feedback_map()
    favs = {"jm": db.collection("jm"), "pica": db.collection("pica")}
    negs = [w for (s, wid), f in fb.items() if f["label"] in ("skip", "dislike") for w in [db.get_work(s, wid)] if w]
    return Profile.build(favs, negs)


def build_taste(db: DB, profile: Profile, clusters: bool = True):
    from .taste import TasteModel, background_works
    fb = db.feedback_map()
    feedback: list = [(w, f["label"]) for (s, wid), f in fb.items() if f["label"] in ("want", "seen_like", "skip", "dislike")
                      for w in [db.get_work(s, wid)] if w]
    cal = db.calibration_map()
    fav_keys = {w.key for w in profile.favorites}
    feedback += [(w, c["level"]) for key, c in cal.items() if key not in fav_keys and c["level"] > 0
                 for w in [db.get_work(*key)] if w and w.detail_ok]
    tm = TasteModel(profile.favorites, profile.fav_weights, background_works(db), feedback,
                    clusters=clusters, calibration=cal)
    if clusters:
        db.kv_set("taste:clusters", len(tm.clusters))
    return tm


def taste_summary(profile: Profile, top: int = 8) -> dict:
    authors: Counter = Counter()
    names: dict[str, str] = {}
    tags: Counter = Counter()
    parodies: Counter = Counter()
    for w, wt in zip(profile.favorites, profile.fav_weights):
        for k, pw, raw in work_people(w):
            if pw == 1.0:
                authors[k] += wt
                names.setdefault(k, raw)
        for t in work_tokens(w):
            if t.startswith("原作:"):
                parodies[t[3:]] += wt
            else:
                tags[t] += wt
        for p in w.parodies:
            names.setdefault("原作:" + norm(p), p)
    # 标签去掉几乎人人都有的（出现率 > 60%）
    n = max(1, len(profile.favorites))
    tag_list = [t for t, c in tags.most_common(top * 3) if c / n < 0.6][:top]
    return {
        "authors": [names[k] for k, _ in authors.most_common(top)],
        "tags": tag_list,
        "parodies": [names.get("原作:" + k, k) for k, _ in parodies.most_common(4)],
    }


def gather_candidates(db: DB, jm: SiteAdapter, profile: Profile, progress: Progress | None,
                      taste=None) -> tuple[dict[str, tuple[Work, str]], dict[str, list[str]]]:
    """返回 ({wid: (作品, 来源路线)}, {wid: 搜到它的标签})。同一作品被多路找到时保留第一路。"""
    from .normalize import norm_tag
    found: dict[str, tuple[Work, str]] = {}
    hints: dict[str, list[str]] = {}

    def add(works: list[Work], route: str, tags: list[str] | None = None) -> None:
        for w in works:
            if w.wid not in found:
                found[w.wid] = (w, route)
            if tags:
                h = hints.setdefault(w.wid, [])
                h += [t for t in (norm_tag(x) for x in tags) if t and t not in h]

    taste_s = taste_summary(profile)
    steps: list[tuple[str, str]] = [("author", a) for a in taste_s["authors"]]
    if taste is not None and taste.clusters:
        # 每个口味簇各搜它最有代表性的标签，小众口味也有自己的候选
        for _, tags in taste.cluster_queries():
            steps.append(("tag", tags[0]))
            if len(tags) > 1:
                steps.append(("tag2", f"{tags[0]} {tags[1]}"))
    else:
        steps += ([("tag", t) for t in taste_s["tags"][:5]] +
                  [("tag2", f"{a} {b}") for a, b in zip(taste_s["tags"][:3], taste_s["tags"][1:4])])
    steps += [("parody", p) for p in taste_s["parodies"]]
    seen_q = set()
    steps = [x for x in steps if not (x in seen_q or seen_q.add(x))]
    for i, (kind, q) in enumerate(steps):
        if progress:
            progress(f"循着“{q}”寻找（{i + 1}/{len(steps)}）", i + 1, len(steps))
        if kind == "author":
            add(_cached_search(db, jm, f"author:{q}", lambda: jm.search(q, "author", 1, "latest")), "author")
        elif kind == "tag":
            add(_cached_search(db, jm, f"tag:{q}", lambda: jm.search(q, "tag", 1, "like")), "tag", [q])
        elif kind == "tag2":
            add(_cached_search(db, jm, f"site:{q}", lambda: jm.search(q, "site", 1, "like")), "tag", q.split())
        elif kind == "parody":
            add(_cached_search(db, jm, f"work:{q}", lambda: jm.search(q, "work", 1, "like")), "parody")

    # 近期禁漫收藏的相关作品（导入时已缓存相关列表）
    for w in db.collection("jm")[:15]:
        ids = db.kv_get(f"related:jm:{w.wid}") or []
        rel = db.get_works([("jm", x) for x in ids])
        add(list(rel.values()), "related")

    if progress:
        progress("顺路看看站内正热的作品")
    add(_cached_search(db, jm, "rank:week:1", lambda: jm.ranking("week", 1)), "explore")
    add(_cached_search(db, jm, "rank:month:1", lambda: jm.ranking("month", 1)), "explore")
    return found, hints


def blocked_tag_set(settings: dict) -> set[str]:
    return {n for n in (norm_tag(t) for t in settings.get("blocked_tags") or []) if n}


def _exclusion(db: DB) -> tuple[set[str], set[str], set[tuple[str, str]]]:
    """已收藏的禁漫 ID、两站收藏（及已表态作品）的去重键、已反馈或已展示过的作品。"""
    jm_ids = {w.wid for w in db.collection("jm")}
    keys: set[str] = set()
    for w in db.collection():
        keys |= dedup_keys(w.title, w.authors)
        k = title_key(w.title)
        if len(k) >= 4:
            keys.add("t:" + k)
    fb = db.feedback_map()
    blocked = set(fb.keys())
    for (s, wid) in fb:
        w = db.get_work(s, wid)
        if w:   # 表过态的作品，它的其他版本也不再推
            keys |= dedup_keys(w.title, w.authors)
    # 前几卷展示过的不再出现（每卷都是新的）；重置过开卷记录的话，只算重置之后的卷
    for r in db.q("SELECT DISTINCT site, wid FROM batch_items WHERE batch_id > ?", (db.batch_offset(),)):
        blocked.add((r["site"], r["wid"]))
    return jm_ids, keys, blocked


# 开卷前选的“这一卷想要什么”。只是在固定打分上加减一点，再调调“不重样”的力度，不碰学到的口味
# div：挑一卷时，跟已经挑中的越像扣得越多（0 = 只看分数，越大越不重样）
DIV_DEFAULT = 0.2
MODES = {
    "balance": {"name": "平衡"},
    "safe":    {"name": "稳妥", "author": 0.15, "fit": 0.10, "explore": 0.5, "div": 0.0, "per_author": 3},
    "fresh":   {"name": "尝鲜", "author": -0.22, "explore": 2.0, "div": 0.5, "per_author": 1, "new_author": 0.08},
    "niche":   {"name": "冷门", "pop": -0.30, "explore": 1.0, "hot": False},
}


def _similarity(cands: list[Work]) -> np.ndarray:
    """候选两两之间有多像（标签 + 原作，0~1）。格式标签不算。"""
    from .themes import FORMAT_TAGS
    docs = [" ".join(t.replace(" ", "_") for t in work_tokens(w) if t not in FORMAT_TAGS) for w in cands]
    if not any(docs):
        return np.zeros((len(cands), len(cands)))
    from sklearn.feature_extraction.text import TfidfVectorizer
    try:
        X = TfidfVectorizer(token_pattern=r"[^ ]+", sublinear_tf=True).fit_transform(docs)
    except ValueError:
        return np.zeros((len(cands), len(cands)))
    S = (X @ X.T).toarray()
    np.fill_diagonal(S, 0.0)
    return S


def _quotas(main_k: int, cinfo: list[dict], rnd: random.Random, power: float = 0.5) -> dict[int, int]:
    """名额按簇占比开平方分（大簇不吃掉整卷），再乘调音给的簇权重；每个有效簇至少 1 本。
    被确认不喜欢的簇（静音）大约每 2～3 卷才出现 1 本。"""
    live = [(ci, c) for ci, c in enumerate(cinfo) if not c.get("muted")]
    raw = {ci: (c["share"] ** power) * c.get("weight", 1.0) for ci, c in live}
    tot = sum(raw.values()) or 1.0
    q = {ci: max(1, round(main_k * v / tot)) for ci, v in raw.items()}
    for ci, c in enumerate(cinfo):
        if c.get("muted"):
            q[ci] = 1 if rnd.random() < 0.4 else 0
    return q


def _pick(cands: list[Work], scores: list[float], routes: list[str], k: int, explore_k: int,
          max_per_author: int, rnd: random.Random, clusters: list[int] | None = None,
          cinfo: list[dict] | None = None, power: float = 0.5, hot_k: int = 1,
          div: float | None = None) -> list[int]:
    """选出一卷。主体有两种挑法：
      div 为 None（旧版）：按分数取，每个口味簇有名额；
      div 为数值（新版 MMR）：一本一本挑，每次挑“分数高、又和已经挑中的不像”的那本；
        也先守口味名额（调音的权重、静音都照旧），名额内再挑不重样的。
    之后探索名额按口味的不确定程度抽（反馈越少、好坏越混杂越常被试探），另留一本站内热门。"""
    order = sorted(range(len(cands)), key=lambda i: -scores[i])
    chosen, per_author, per_parody, per_cluster = [], Counter(), Counter(), Counter()
    seen_keys: set[str] = set()
    ckeys = [dedup_keys(w.title, w.authors) for w in cands]
    clusters = clusters or [-1] * len(cands)
    cinfo = cinfo or []
    main_k = k - explore_k
    quota = _quotas(main_k, cinfo, rnd, power) if cinfo else {}
    muted = {ci for ci, c in enumerate(cinfo) if c.get("muted")}

    def ok(i: int) -> bool:
        if ckeys[i] & seen_keys:   # 同一本的另一个版本已经在这卷里了
            return False
        people = [p for p, _, _ in work_people(cands[i])][:1]
        if any(per_author[p] >= max_per_author for p in people):
            return False
        par = [norm(p) for p in cands[i].parodies][:1]
        if any(per_parody[p] >= max(3, k // 4) for p in par):
            return False
        c = clusters[i]
        if c in muted and per_cluster[c] >= quota.get(c, 0):
            return False
        return True

    def take(i: int) -> None:
        chosen.append(i)
        seen_keys.update(ckeys[i])
        for p, _, _ in work_people(cands[i])[:1]:
            per_author[p] += 1
        for p in cands[i].parodies[:1]:
            per_parody[norm(p)] += 1
        per_cluster[clusters[i]] += 1

    if div is not None:
        # 1) 主体（MMR）：先守口味名额（每种味道都照顾到），名额内一本一本挑
        #    “分数高、又和已经挑中的不像”的；名额用完后不再限口味，照样这么挑
        s = np.asarray(scores, dtype=float)
        span = s.max() - s.min() if len(s) else 0.0
        rel = (s - s.min()) / span if span > 0 else np.zeros(len(s))
        sim = _similarity(cands)
        maxsim = np.zeros(len(cands))
        for use_quota in ((True, False) if quota else (False,)):
            while len(chosen) < main_k:
                best, best_v = None, -1e9
                for i in order:
                    if i in chosen or not ok(i):
                        continue
                    c = clusters[i]
                    if use_quota and c in quota and per_cluster[c] >= quota[c]:
                        continue
                    v = (1 - div) * rel[i] - div * maxsim[i]
                    if v > best_v:
                        best, best_v = i, v
                if best is None:
                    break
                take(best)
                maxsim = np.maximum(maxsim, sim[best])
    else:
        # 1) 主体：遵守口味簇名额
        for i in order:
            if len(chosen) >= main_k:
                break
            c = clusters[i]
            if c in quota and per_cluster[c] >= quota[c]:
                continue
            if ok(i):
                take(i)
        for i in order:   # 名额没用完的簇，空位按分数补
            if len(chosen) >= main_k:
                break
            if i not in chosen and ok(i):
                take(i)
    # 2) 一本站内热门（偶遇）
    hot = [i for i in order if routes[i] == "explore" and i not in chosen]
    rnd.shuffle(hot)
    for i in hot[:hot_k]:
        if len(chosen) < k and ok(i):
            take(i)
    # 3) 试探：先按不确定程度抽一个簇，再取这簇里分数最高、还没选的
    live = [ci for ci in range(len(cinfo)) if ci not in muted]
    tries = 0
    while len(chosen) < k and live and tries < 4 * k:
        tries += 1
        ci = rnd.choices(live, weights=[cinfo[c].get("uncertainty", 1.0) + 0.05 for c in live])[0]
        pool = [i for i in order if clusters[i] == ci and i not in chosen and ok(i)][:3]
        if not pool:
            live.remove(ci)
            continue
        take(pool[0])
    for i in order:  # 不够时补满
        if len(chosen) >= k:
            break
        if i not in chosen and ok(i):
            take(i)
    for i in order:  # 还不够：放宽“同一作者 / 同一原作”的限制，只保证不是同一本的不同版本
        if len(chosen) >= k:
            break
        if i not in chosen and not (ckeys[i] & seen_keys):
            take(i)
    return chosen


def reason_text(reason: dict, route: str) -> str:
    parts = []
    if route == "explore" and not reason.get("author") and not reason.get("nearest"):
        return "站内正热，和你平日的口味不太一样，换换口味"
    if route == "cold":
        return "题材各异的热门作品，挑几本告诉我心不心动"
    if reason.get("author"):
        parts.append(f"出自你熟悉的{reason.get('author_kind', '作者')}「{reason['author']}」")
    if reason.get("nearest"):
        titles = "》《".join(n["title"][:24] for n in reason["nearest"][:2])
        parts.append(f"与你收藏的《{titles}》气味相近")
    if reason.get("shared_tags"):
        parts.append("同样带着 " + "、".join(reason["shared_tags"][:3]))
    if not parts:
        parts.append(f"循着“{ROUTE_NAMES.get(route, route)}”找到")
    return "；".join(parts)


def _head(pool: list[tuple[Work, str]], order: list[int], n: int, per_author: int) -> list[tuple[Work, str]]:
    """挑出要细读的候选：按初筛分数取，但同一作者最多 per_author 本，
    “熟悉作者”这条路最多占一半，免得整卷都是老作者的其他作品。不够再按分数补。"""
    head, used, by_author = [], set(), Counter()
    author_cap = n // 2
    for i in order:
        if len(head) >= n:
            break
        w, route = pool[i]
        who = ([p for p, _, _ in work_people(w)][:1] or [""])[0]
        if who and by_author[who] >= per_author:
            continue
        if route == "author" and sum(1 for _, r in head if r == "author") >= author_cap:
            continue
        head.append(pool[i])
        used.add(i)
        if who:
            by_author[who] += 1
    for i in order:
        if len(head) >= n:
            break
        if i not in used:
            head.append(pool[i])
            used.add(i)
    return head


def generate_batch(db: DB, jm: SiteAdapter, progress: Progress | None = None, seed: int | None = None,
                   mode: str | None = None) -> int:
    settings = load_settings()
    k = int(settings.get("batch_size", 10))
    mode = mode if mode in MODES else (settings.get("rec_mode") if settings.get("rec_mode") in MODES else "balance")
    mc = MODES[mode]
    rnd = random.Random(seed if seed is not None else time.time())

    semantic = get_semantic(db, settings)
    sem_error = ""

    if progress:
        progress("翻阅你的收藏，整理口味")
    from .taste import background_stale, learn_aliases, refresh_background
    try:
        learn_aliases(db)
    except Exception as e:  # 对照表学不出来不影响推荐
        import logging
        logging.getLogger("honmei").warning("跨站标签对照没学成：%s", e)
    profile = build_profile(db)
    if not profile.empty and background_stale(db):
        try:
            refresh_background(db, jm, progress)
        except SiteError as e:
            import logging
            logging.getLogger("honmei").warning("基准样本没取全：%s", e)
    taste = None if profile.empty else build_taste(db, profile)
    hints: dict[str, list[str]] = {}
    if profile.empty:
        # 完全没有收藏：展示一批题材分散的热门作品
        found = {}
        for kind in ("week", "month"):
            for w in _cached_search(db, jm, f"rank:{kind}:1", lambda: jm.ranking(kind, 1)):
                found.setdefault(w.wid, (w, "cold"))
    else:
        found, hints = gather_candidates(db, jm, profile, progress, taste)

    jm_ids, keys, blocked = _exclusion(db)
    banned = blocked_tag_set(settings)
    pool: list[tuple[Work, str]] = []
    for wid, (w, route) in found.items():
        if wid in jm_ids or ("jm", wid) in blocked:
            continue
        if banned and banned & set(norm_tags(w.tags)):
            continue
        if dedup_keys(w.title, w.authors) & keys:
            continue  # 已收藏（含另一站）或表过态的同一本的其他版本
        pool.append((w, route))
    if not pool:
        raise SiteError("没有找到新的候选，可能是网络不通或收藏还没导入")

    # 粗排：用列表信息（标题拆出的作者、标记）先算一遍，挑前面的补详情
    if progress:
        progress(f"从 {len(pool)} 本候选里初筛")
    background = [w for w, _ in pool]
    fb = FeatureBuilder(profile, background, None, taste=taste, hints=hints)
    rows, _ = fb.build(background)
    a_w = max(0.0, 0.3 + mc.get("author", 0))       # 尝鲜模式初筛就少看作者
    pre = [0.25 * r["tag"] + 0.3 * r["fit"] + a_w * r["author"] + 0.15 * r["title"] for r in rows]
    order = sorted(range(len(pool)), key=lambda i: -pre[i])
    head = _head(pool, order, min(len(pool), k * 5), per_author=max(2, mc.get("per_author", 2) + 1))
    explore_extra = [p for p in pool if p[1] in ("explore", "cold") and p not in head][: max(4, k // 4)]
    head += explore_extra

    ensure_details(db, jm, [w.wid for w, _ in head], progress, label="细读候选：")
    cands, routes = [], []
    for w, route in head:
        full = db.get_work("jm", w.wid) or w
        if banned and banned & set(norm_tags(full.tags)):
            continue   # 细读后才看到的标签
        cands.append(full)
        routes.append(route)

    if progress:
        progress("掂量每一本与你的距离")
    try:
        fb = FeatureBuilder(profile, cands + [w for w, _ in pool], semantic, taste=taste)
        feats, reasons = fb.build(cands)
    except SemanticUnavailable as e:
        sem_error = str(e)
        semantic = None
        fb = FeatureBuilder(profile, cands + [w for w, _ in pool], None, taste=taste)
        feats, reasons = fb.build(cands)

    state = train(db, semantic is not None)
    scores, engine = final_scores(feats, semantic is not None, state)
    cl = taste.cluster_of(cands) if taste is not None else None
    if cl is not None:
        # 调音里的“方向”：偏实用的簇多看标签契合，偏剧情的簇多看简介语义
        for i, ci in enumerate(cl):
            if ci >= 0:
                c = taste.clusters[ci]
                scores[i] += 0.1 * c.get("practical", 0) * (feats[i]["fit"] - 0.5) \
                    + 0.1 * c.get("story", 0) * feats[i].get("semantic", 0)

    # 这一卷的口味开关
    if mode != "balance":
        for i, f in enumerate(feats):
            scores[i] += mc.get("author", 0) * f["author"] + mc.get("fit", 0) * (f["fit"] - 0.5) \
                + mc.get("pop", 0) * f["pop"] + mc.get("new_author", 0) * (1.0 if f["author"] < 0.05 else 0.0)

    explore_k = 0 if profile.empty else max(1, round(k * float(settings.get("explore_ratio", 0.15)) * mc.get("explore", 1.0)))
    cinfo = taste.clusters if taste is not None else None
    per_author = mc.get("per_author", int(settings.get("max_per_author", 2)))
    chosen = _pick(cands, scores, routes, k, explore_k, per_author, rnd, cl, cinfo,
                   hot_k=1 if mc.get("hot", True) else 0, div=mc.get("div", DIV_DEFAULT))

    rnd.shuffle(chosen)   # 卷里的次序打乱：No.1 不一定是最契合的，翻开才知道
    items = []
    for i in chosen:
        r = dict(reasons[i])
        r["text"] = reason_text(r, routes[i])
        r["route_name"] = ROUTE_NAMES.get(routes[i], routes[i])
        if cl is not None and cl[i] >= 0:
            r["cluster"] = cl[i]
            r["cluster_tags"] = taste.clusters[cl[i]]["tags"][:3]
        items.append({"site": "jm", "wid": cands[i].wid, "score": round(scores[i], 4),
                      "features": feats[i], "reason": r, "route": routes[i]})
    meta = {"pool": len(pool), "ranker": state.get("mode"), "ranker_message": state.get("message", ""),
            "semantic_error": sem_error, "algo": "v2", "mode": mode, "mode_name": mc["name"],
            "background": len(taste.background) if taste is not None else 0,
            "favorites": {s: len(db.collection(s)) for s in ("jm", "pica")}}
    bid = db.create_batch(engine, items, meta)
    if progress:
        progress(f"Vol.{bid - db.batch_offset()} 已成卷，共 {len(items)} 本")
    return bid


def batch_view(db: DB, batch_id: int) -> dict | None:
    meta = db.batch_meta(batch_id)
    if not meta:
        return None
    fb = db.feedback_map()
    banned = blocked_tag_set(load_settings())
    items = []
    for it in db.batch_items(batch_id):
        w = db.get_work(it["site"], it["wid"])
        if not w:
            continue
        f = fb.get((it["site"], it["wid"]))
        pt = parse_title(w.title)
        items.append({
            "pos": it["pos"], "site": it["site"], "wid": it["wid"],
            "title": w.title, "core_title": pt.core or w.title,
            "authors": w.authors, "tags": w.tags[:12], "parodies": w.parodies,
            "cover": f"/api/cover/{w.site}/{w.wid}", "url": w.url,
            "source": "禁漫" if w.site == "jm" else "哔咔",
            "reason": it["reason"], "route": it["route"], "score": it["score"], "features": it["features"],
            "feedback": f["label"] if f else None,
            "blocked": [t for t in w.tags if norm_tag(t) in banned],
        })
    meta["items"] = items
    return meta


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, indent=2)
