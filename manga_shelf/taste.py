"""口味模型：每个标签你有多偏爱、你的收藏分成哪几簇。

标签偏爱度用“提升度”而不是出现次数：
    你收藏里带这个标签的比例 ÷ 站内一般作品里带它的比例
站内一般比例来自一份“基准样本”（周榜、月榜、最新更新、随机分类页，约 300 本，取详情后缓存两周）。
没有基准样本时，退回到和“平均标签”比，并把偏爱度整体减半（参照不可靠，少下结论）。

调音（calibration）给收藏分层：本命 ×2、喜欢 ×1.3、一般 ×0.5、不太喜欢 ×0。

两站标签写法不同（哔咔平均 12 个标签，禁漫 6～7 个），所以一个标签的比例只在
“用得到这个标签的站”的收藏里算，不会因为哔咔收藏里没有禁漫特有的标签就被判成不喜欢。

心动 / 略过会直接调整相关标签的偏爱度，几条反馈就能看出变化。
"""
from __future__ import annotations

import math
import time
from collections import Counter, defaultdict

import numpy as np

from .db import DB
from .features import work_tokens
from .models import Work

BG_KEY = "background:jm"
BG_TTL = 14 * 24 * 3600
BG_RANK = 80         # 榜单最多占多少本（热门作品标签分布偏，不能多）
BG_SIZE = 300
PRIOR = 30.0          # 收缩强度：收藏里出现很少的标签，偏爱度向 0 靠
FEEDBACK_WEIGHT = 0.8
CALIB_MULT = {4: 2.0, 3: 1.3, 2: 0.5, 1: 0.0}
CALIB_VALUE = {4: 1.0, 3: 0.5, 2: -0.3, 1: -1.0}
LEVEL_NAMES = {4: "本命", 3: "喜欢", 2: "一般", 1: "其实不太喜欢", 0: "跳过"}


def background_works(db: DB, sources: tuple[str, ...] | None = None) -> list[Work]:
    hit = db.kv_get(BG_KEY) or {}
    src = hit.get("src") or {}
    ids = [w for w in hit.get("ids", []) if sources is None or src.get(w, "rank") in sources]
    works = db.get_works([("jm", w) for w in ids])
    return [w for w in works.values() if w.detail_ok]


def background_stale(db: DB) -> bool:
    hit = db.kv_get(BG_KEY) or {}
    return not hit.get("ids") or time.time() - hit.get("at", 0) > BG_TTL


def refresh_background(db: DB, jm, progress=None, seed: int | None = None) -> int:
    """取一份站内基准样本：周榜、月榜（最多 80 本）+ 最新更新前两页 + 随机两个分类页，约 300 本，补详情。"""
    import random

    from .importer import ensure_details
    from .sources import SiteError
    rnd = random.Random(seed if seed is not None else time.time())
    cats = list(getattr(jm, "BROWSE_CATEGORIES", []) or ["all"])
    plan = [("rank", lambda: jm.ranking("week", 1)), ("rank", lambda: jm.ranking("month", 1)),
            ("latest", lambda: jm.browse(1)), ("latest", lambda: jm.browse(2))]
    for _ in range(2):
        c, pg = rnd.choice(cats), rnd.randint(2, 40)
        plan.append(("random", lambda c=c, pg=pg: jm.browse(pg, c)))
    ids: list[str] = []
    src: dict[str, str] = {}
    n_rank = 0
    for i, (kind, fn) in enumerate(plan):
        if progress:
            progress(f"在站里随手翻几页，取一份基准样本（{i + 1}/{len(plan)}）", i + 1, len(plan))
        try:
            works = fn()
        except (SiteError, NotImplementedError):
            continue
        for w in works:
            if w.wid in src or (kind == "rank" and n_rank >= BG_RANK):
                continue
            db.upsert_work(w)
            ids.append(w.wid)
            src[w.wid] = kind
            n_rank += kind == "rank"
    if len(ids) > BG_SIZE:
        keep = [w for w in ids if src[w] == "rank"] + rnd.sample([w for w in ids if src[w] != "rank"],
                                                               BG_SIZE - n_rank)
        ids = [w for w in ids if w in set(keep)]
    if not ids:
        return 0
    ensure_details(db, jm, ids, progress, label="基准样本：")
    db.kv_set(BG_KEY, {"at": time.time(), "ids": ids, "src": {w: src[w] for w in ids}})
    return len(ids)


# ---------- 跨站标签对照 ----------
def learn_aliases(db: DB, min_pairs: int = 2, min_ratio: float = 0.6) -> dict[str, str]:
    """两站都有的同一本（按核心标题匹配），统计哔咔标签和禁漫标签的共现，
    共现率高、且这个哔咔标签在禁漫从没出现过的，记成同义（哔咔说法 → 禁漫说法）。"""
    import json

    from .config import data_dir
    from .normalize import norm_tags, reset_aliases, title_key
    pica = {}
    for w in db.collection("pica"):
        k = title_key(w.title)
        if len(k) >= 4 and w.tags:
            pica[k] = w
    jm_vocab: set[str] = set()
    pairs = []
    for r in db.q("SELECT * FROM works WHERE site='jm' AND detail_ok=1"):
        w = db._row_to_work(r)
        jt = set(norm_tags(w.tags))
        jm_vocab |= jt
        p = pica.get(title_key(w.title))
        if p is not None and jt:
            pairs.append((set(norm_tags(p.tags)), jt))
    co, n_p, n_j = Counter(), Counter(), Counter()
    for pt, jt in pairs:
        n_p.update(pt)
        n_j.update(jt)
        for a in pt - jt:
            for b in jt - pt:
                co[(a, b)] += 1
    learned: dict[str, str] = {}
    for (a, b), c in co.most_common():
        if a in jm_vocab or a in learned or c < min_pairs:
            continue
        if c / n_p[a] >= min_ratio and c / n_j[b] >= min_ratio:
            learned[a] = b
    path = data_dir() / "learned_aliases.json"
    try:
        old = json.loads(path.read_text("utf-8")) if path.exists() else {}
    except Exception:
        old = {}
    if learned != old:
        path.write_text(json.dumps(learned, ensure_ascii=False, indent=1), "utf-8")
        reset_aliases()
    db.kv_set("aliases:learned", {"at": time.time(), "pairs": len(pairs), "aliases": learned})
    return learned


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


class TasteModel:
    def __init__(self, favorites: list[Work], fav_weights, background: list[Work],
                 feedback: list[tuple[Work, str]] | None = None, clusters: bool = True,
                 calibration: dict | None = None, cluster_algo: str = "nmf"):
        self.favorites = favorites
        self.calibration = calibration or {}
        w = np.asarray(fav_weights, dtype=float) if len(favorites) else np.zeros(0)
        mult = np.array([CALIB_MULT.get((self.calibration.get(f.key) or {}).get("level", -1), 1.0)
                         for f in favorites]) if len(favorites) else np.zeros(0)
        self.weights = w * mult
        self.background = background
        fav_tokens = [set(work_tokens(w)) for w in favorites]
        bg_tokens = [set(work_tokens(w)) for w in background]

        # 每站用到过哪些标签（收藏 + 基准样本）
        site_vocab: dict[str, set[str]] = defaultdict(set)
        for w, toks in zip(favorites, fav_tokens):
            site_vocab[w.site] |= toks
        for w, toks in zip(background, bg_tokens):
            site_vocab[w.site] |= toks
        site_mass = Counter()
        for w, wt in zip(favorites, self.weights):
            site_mass[w.site] += wt

        fav_mass: Counter = Counter()
        for toks, wt in zip(fav_tokens, self.weights):
            for t in toks:
                fav_mass[t] += wt
        bg_count: Counter = Counter()
        for toks in bg_tokens:
            bg_count.update(toks)
        n_bg = len(background)
        self.has_background = n_bg >= 30

        # 没有基准样本时，用收藏里“平均标签”的比例当参照
        rates = []
        denom: dict[str, float] = {}
        for t in fav_mass:
            d = sum(site_mass[s] for s, v in site_vocab.items() if t in v)
            denom[t] = max(d, 1e-9)
            rates.append(fav_mass[t] / denom[t])
        flat_ref = float(np.mean(rates)) if rates else 0.05

        self.pref: dict[str, float] = {}
        self.fav_rate: dict[str, float] = {}
        self.bg_rate: dict[str, float] = {}
        vocab = set(fav_mass) | set(bg_count)
        for t in vocab:
            p_bg = (bg_count[t] + 0.5) / (n_bg + 1) if self.has_background else flat_ref
            d = denom.get(t) or sum(site_mass[s] for s, v in site_vocab.items() if t in v)
            n_t = fav_mass.get(t, 0.0)
            p_fav = (n_t + PRIOR * p_bg) / (d + PRIOR) if d > 0 else p_bg
            self.fav_rate[t] = n_t / d if d > 0 else 0.0
            self.bg_rate[t] = p_bg
            lift = math.log(p_fav / p_bg)
            if not self.has_background:
                lift *= 0.5
            self.pref[t] = float(np.clip(lift, -2.0, 2.0))

        # 心动 / 略过：按标签累计，带收缩
        self.feedback_adj: dict[str, float] = {}
        if feedback:
            want, skip = Counter(), Counter()
            for w, label in feedback:
                if isinstance(label, int):   # 调音评分：本命/喜欢 = 心动，不太喜欢 = 略过
                    label = "want" if label >= 3 else "skip" if label == 1 else ""
                if label == "seen_like":     # 读过而且喜欢 = 心动
                    label = "want"
                for t in set(work_tokens(w)):
                    if label == "want":
                        want[t] += 1
                    elif label == "skip":
                        skip[t] += 1
                    elif label == "dislike":   # 不喜欢比略过分量重
                        skip[t] += 2
            for t in set(want) | set(skip):
                n = want[t] + skip[t]
                adj = math.log((want[t] + 1) / (skip[t] + 1)) * n / (n + 3)
                self.feedback_adj[t] = adj
                self.pref[t] = float(np.clip(self.pref.get(t, 0.0) + FEEDBACK_WEIGHT * adj, -2.5, 2.5))

        self.feedback = feedback or []
        self.clusters: list[dict] = []
        self._themes = None
        self._km = None
        if clusters and cluster_algo == "kmeans":     # 旧版（K-means 硬分组），只留着做对比评测
            self._fit_kmeans(fav_tokens)
            self._cluster_signals()
        elif clusters:
            self._fit_clusters()
            self._cluster_signals()

    # ---- 单本打分 ----
    def raw_fit(self, w: Work, extra_tokens: list[str] | None = None) -> float:
        toks = set(work_tokens(w)) | set(extra_tokens or [])
        if not toks:
            return 0.0
        s = sum(self.pref.get(t, 0.0) for t in toks)
        return s / math.sqrt(max(len(toks), 3))

    def fit(self, w: Work, extra_tokens: list[str] | None = None) -> float:
        """0~1，0.5 表示和站内一般作品差不多，越高越合你口味。"""
        return _sigmoid(1.5 * self.raw_fit(w, extra_tokens))

    def top_reasons(self, w: Work, n: int = 3) -> list[str]:
        toks = [t for t in set(work_tokens(w)) if self.pref.get(t, 0) > 0.2]
        return sorted(toks, key=lambda t: -self.pref[t])[:n]

    # ---- 口味簇 ----
    def _fit_clusters(self) -> None:
        """口味分组：NMF 软主题（见 themes.py）。基准样本只用来判断“两站都用的标签”。"""
        from .themes import Themes
        th = Themes(self.favorites, self.background)
        if th.clusters:
            self._themes = th
            self.clusters = th.clusters

    def _fit_kmeans(self, fav_tokens: list[set[str]]) -> None:
        from .insights import _clusters
        lists = [sorted(t for t in toks if not t.startswith("原作:")) for toks in fav_tokens]
        res = _clusters(lists, return_model=True)
        if res:
            self.clusters, self._vec, self._km = res

    def cluster_of(self, works: list[Work]) -> list[int]:
        if self._km is not None and works:
            docs = [" ".join(t.replace(" ", "_") for t in work_tokens(w) if not t.startswith("原作:")) for w in works]
            X = self._vec.transform(docs)
            labels = self._km.predict(X)
            order = {c["label"]: i for i, c in enumerate(self.clusters)}
            empty = np.asarray(X.sum(axis=1)).ravel() == 0
            return [-1 if empty[i] else order.get(int(labels[i]), -1) for i in range(len(works))]
        if self._themes is None or not works:
            return [-1] * len(works)
        return self._themes.assign(works)

    def cluster_mix(self, works: list[Work]) -> np.ndarray | None:
        """每本书在各个口味上的占比（软归属）。"""
        if self._themes is None or not works:
            return None
        return self._themes.mixture(works)

    def _cluster_signals(self) -> None:
        """每簇汇总调音评分和心动/略过：权重、是否静音、不确定程度、看重什么。"""
        if not self.clusters:
            return
        sig: dict[int, list[float]] = defaultdict(list)
        levels: dict[int, list[int]] = defaultdict(list)
        reasons: dict[int, Counter] = defaultdict(Counter)
        member_of = {}
        for ci, c in enumerate(self.clusters):
            for i in c["members"]:
                member_of[i] = ci
        for i, f in enumerate(self.favorites):
            cal = self.calibration.get(f.key)
            if cal and cal["level"] in CALIB_VALUE and i in member_of:
                ci = member_of[i]
                sig[ci].append(CALIB_VALUE[cal["level"]])
                levels[ci].append(cal["level"])
                reasons[ci].update(cal.get("reasons") or [])
        others = [(w, lab) for w, lab in self.feedback]
        if others:
            for (w, lab), ci in zip(others, self.cluster_of([w for w, _ in others])):
                if ci < 0:
                    continue
                if isinstance(lab, int):
                    if lab in CALIB_VALUE:
                        sig[ci].append(CALIB_VALUE[lab])
                        levels[ci].append(lab)
                else:
                    sig[ci].append(0.5 if lab in ("want", "seen_like") else -0.5)
        for ci, c in enumerate(self.clusters):
            v = sig.get(ci, [])
            n = len(v)
            mean = sum(v) / (n + 2)   # 收缩：评得越多越可信
            c["weight"] = round(float(np.clip(math.exp(mean), 0.3, 2.0)), 3)
            lv = levels.get(ci, [])
            c["muted"] = lv.count(1) >= 2 and not any(x >= 3 for x in lv)
            spread = float(np.std(v)) if n >= 2 else 0.0
            c["uncertainty"] = round(1 / math.sqrt(n + 1) + spread, 3)
            c["n_signals"] = n
            rc = reasons.get(ci, Counter())
            tot = max(1, sum(1 for f_i in c["members"]
                             if (self.calibration.get(self.favorites[f_i].key) or {}).get("reasons")))
            c["practical"] = round(rc["实用"] / tot, 2)
            c["story"] = round(rc["剧情"] / tot, 2)
            c["art"] = round((rc["艺术"] + rc["画风"] + rc["题材"]) / tot, 2)
            c["name"] = "·".join(c["tags"][:2])

    def cluster_distances(self, works: list[Work]) -> np.ndarray | None:
        if self._themes is None or not works:
            return None
        return self._themes.distances(works)

    def cluster_queries(self, per_cluster: int = 2, min_share: float = 0.06) -> list[tuple[int, list[str]]]:
        """每个口味簇挑几个最有代表性的标签，用来去站里搜。"""
        out, used = [], set()
        for ci, c in enumerate(self.clusters):
            if c["share"] < min_share:
                continue
            members = c["members"]
            cnt: Counter = Counter()
            for i in members:
                cnt.update(t for t in work_tokens(self.favorites[i]) if not t.startswith("原作:"))
            m = max(1, len(members))

            def score(t: str) -> float:
                inside = cnt[t] / m
                distinct = min(inside / max(self.fav_rate.get(t, 0.0), 0.02), 5.0)   # 在这一簇里比平时多见几倍
                return inside * distinct * max(self.pref.get(t, 0.0) + 0.3, 0.05)
            ranked = [t for t in sorted(cnt, key=lambda t: -score(t)) if t not in used and cnt[t] / m >= 0.2]
            pick = ranked[:per_cluster]
            used.update(pick[:1])
            if pick:
                out.append((ci, pick))
        return out
