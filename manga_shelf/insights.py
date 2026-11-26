"""口味统计：给“本命报告”看板和 AI 画像用的同一份摘要。

只用收藏的标题、作者、标签、原作和收藏先后顺序，全部在本地计算。
两站都没有收藏的具体日期，“早期 / 近期”按收藏顺序切分（最新 25% 与最早 25%）。
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict

import numpy as np

from .db import DB
from .features import work_tokens
from .models import Work
from .normalize import norm, norm_parody, norm_person, norm_tags, title_key, to_simplified

# 标签维度：关键词出现在规范化标签里就归入该维度（按顺序匹配第一个）
DIMENSIONS: list[tuple[str, list[str]]] = [
    ("关系与情节", ["纯爱", "恋爱", "ntr", "出轨", "后宫", "乱伦", "近亲", "催眠", "调教", "强暴", "凌辱", "奸",
                   "百合", "耽美", "师生", "青梅", "初恋", "交换", "援交", "卖春", "偷窥", "露出", "支配", "榨精",
                   "逆推", "堕落", "恶堕", "洗脑", "常识改变", "时间停止", "睡眠", "痴汉", "女性支配", "男性受"]),
    ("角色身份", ["妹", "姐", "人妻", "熟女", "母", "学生", "jk", "女高中生", "老师", "女仆", "护士", "辣妹", "巫女",
                 "修女", "偶像", "大小姐", "魅魔", "精灵", "兽耳", "猫娘", "非人", "怪物", "伪娘", "扶他", "萝莉",
                 "正太", "学妹", "学姐", "上司", "邻居", "青梅竹马", "处女", "小恶魔", "无口", "傲娇", "痴女"]),
    ("身体特征", ["巨乳", "贫乳", "爆乳", "大乳晕", "乳", "肉感", "肌肉", "褐", "黑皮", "多毛", "腋", "足", "腿", "臀",
                 "孕", "怀孕", "眼镜", "短发", "长发", "双马尾", "马尾", "黑长直", "单马尾", "痣"]),
    ("服装", ["校服", "制服", "过膝袜", "长筒袜", "连裤", "丝袜", "泳装", "内衣", "情趣", "兔女郎", "女仆装", "体操服",
             "旗袍", "和服", "比基尼", "围裙", "睡衣", "运动服", "cosplay", "项圈"]),
    ("画面与表现", ["横切面", "透视", "阿黑颜", "阿嘿颜", "蹦坏脸", "颜射", "口爆", "潮吹", "失禁", "汗", "精液", "淫纹",
                   "无修正", "全彩", "黑白", "ai"]),
    ("篇幅与形式", ["长篇", "短篇", "单行本", "剧情", "同人", "cg", "杂志", "合集", "韩漫", "3d", "连载", "完结"]),
]

# 用来算“人格指数”的标签组（命中任一关键词即计入）
AXES = {
    "纯爱": ["纯爱", "恋爱", "初恋", "接吻", "甜", "后宫闪光", "青梅"],
    "禁忌": ["ntr", "出轨", "乱伦", "近亲", "寝取", "交换", "援交"],
    "被支配": ["女性支配", "榨精", "逆推", "痴女", "魅魔", "男性受", "骑乘", "骑乗", "小恶魔"],
    "支配": ["调教", "催眠", "强暴", "凌辱", "洗脑", "常识改变", "时间停止", "束缚", "项圈", "堕落", "恶堕"],
    "剧情": ["剧情", "长篇", "单行本", "连载"],
    "丰满": ["巨乳", "爆乳", "大乳晕", "肉感", "乳交"],
    "娇小": ["贫乳", "萝莉", "小只"],
    "制服": ["校服", "制服", "女高中生", "jk", "学生", "体操服", "女仆"],
    "腿控": ["过膝袜", "长筒袜", "连裤", "丝袜", "足", "腿"],
    "重口": ["阿黑颜", "阿嘿颜", "蹦坏脸", "失禁", "触手", "怪物", "重口", "猎奇", "肛"],
}

NOISY_AUTHORS = {"3d", "急求", "多作者", "よろず", "anthology", "アンソロジー", "未知", "unknown", "ai", "pixiv"}


def _match(tag: str, keys: list[str]) -> bool:
    return any(k in tag for k in keys)


def dimension_of(tag: str) -> str | None:
    for name, keys in DIMENSIONS:
        if _match(tag, keys):
            return name
    return None


def _favorites(db: DB) -> list[tuple[Work, int]]:
    rows = db.q("SELECT w.*, c.rank FROM collections c JOIN works w ON w.site=c.site AND w.wid=c.wid "
                "ORDER BY c.site, c.rank")
    return [(db._row_to_work(r), r["rank"]) for r in rows]


def _share(items: list[set[str]], keys: list[str]) -> float:
    if not items:
        return 0.0
    return sum(1 for tags in items if any(_match(t, keys) for t in tags)) / len(items)


def _clusters(tag_sets: list[list[str]], k_max: int = 6, return_model: bool = False):
    """把收藏按标签聚成几类口味（TF-IDF + KMeans，按轮廓系数选 k）。"""
    n = len(tag_sets)
    if n < 20:
        return []
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics import silhouette_score
    docs = [" ".join(t.replace(" ", "_") for t in ts) for ts in tag_sets]
    vec = TfidfVectorizer(token_pattern=r"[^ ]+", min_df=3, max_df=0.5, sublinear_tf=True)
    try:
        X = vec.fit_transform(docs)
    except ValueError:
        return []
    if X.shape[1] < 5:
        return []
    best = None
    for k in range(3, min(k_max, n // 15) + 1):
        km = KMeans(n_clusters=k, n_init=8, random_state=7).fit(X)
        try:
            s = silhouette_score(X, km.labels_, sample_size=min(600, n), random_state=7)
        except ValueError:
            continue
        if best is None or s > best[0]:
            best = (s, k, km)
    if not best:
        return []
    _, k, km = best
    vocab = np.array(vec.get_feature_names_out())
    out = []
    for c in range(k):
        idx = np.where(km.labels_ == c)[0]
        center = km.cluster_centers_[c]
        top = [vocab[i].replace("_", " ") for i in np.argsort(-center)[:6]]
        out.append({"label": c, "size": int(len(idx)), "share": round(len(idx) / n, 3), "tags": top,
                    "members": idx.tolist()})
    out.sort(key=lambda c: -c["size"])
    return (out, vec, km) if return_model else out


def _brief(w: Work) -> dict:
    from .normalize import parse_title
    return {"site": w.site, "wid": w.wid, "title": parse_title(w.title).core or w.title,
            "authors": w.authors[:2], "cover": f"/api/cover/{w.site}/{w.wid}", "url": w.url}


HIDDEN_KEY = "report:hidden_tags"


def hidden_tags(db: DB) -> list[str]:
    return list(db.kv_get(HIDDEN_KEY) or [])


def set_hidden(db: DB, tag: str, hidden: bool) -> list[str]:
    cur = hidden_tags(db)
    t = (norm_tags([tag]) or [norm(tag)])[0] if tag else ""
    if not t:
        return cur
    cur = [x for x in cur if x != t] + ([t] if hidden else [])
    db.kv_set(HIDDEN_KEY, cur)
    return cur


def summary(db: DB) -> dict:
    favs = _favorites(db)
    if not favs:
        return {"empty": True}
    hidden = set(hidden_tags(db))     # 报告里屏蔽的词：从所有统计里拿掉（只影响报告和侧写，不影响推荐）
    per_site = Counter(w.site for w, _ in favs)
    n = len(favs)

    tag_sets: list[list[str]] = []
    tag_count: Counter = Counter()
    author_count: Counter = Counter()
    author_name: dict[str, str] = {}
    author_works: dict[str, list[Work]] = defaultdict(list)
    parody_count: Counter = Counter()
    parody_name: dict[str, str] = {}
    group_count: Counter = Counter()
    color = long_ = 0
    for w, _ in favs:
        ts = [t for t in norm_tags(w.tags) if t not in hidden]
        tag_sets.append(ts)
        tag_count.update(set(ts))
        for a in set(w.authors):
            k = norm_person(a)
            if k and k not in NOISY_AUTHORS and len(k) > 1:
                author_count[k] += 1
                author_name.setdefault(k, a)
                author_works[k].append(w)
        for p in {norm_parody(x) for x in w.parodies}:
            k = norm(p)
            if k and "comic" not in k and "快乐天" not in k and k != "ai":
                parody_count[k] += 1
                parody_name.setdefault(k, p)
        for g in w.groups[:1]:
            group_count[to_simplified(g)] += 1
        color += "全彩" in ts
        long_ += "长篇" in ts

    # 维度分布
    dims: dict[str, Counter] = defaultdict(Counter)
    for t, c in tag_count.items():
        d = dimension_of(t)
        if d:
            dims[d][t] = c
    dimensions = [
        {"name": name, "top": [{"tag": t, "count": c, "share": round(c / n, 3)} for t, c in dims[name].most_common(8)]}
        for name, _ in DIMENSIONS if dims.get(name)
    ]

    # 人格指数：命中比例
    sets = [set(ts) for ts in tag_sets]
    axes = {k: round(_share(sets, v), 3) for k, v in AXES.items()}

    # 新旧变化（每个站分别按顺序切，再合并）
    by_site: dict[str, list[tuple[list[str], int]]] = defaultdict(list)
    for (w, rank), ts in zip(favs, tag_sets):
        by_site[w.site].append((ts, rank))
    recent, early = Counter(), Counter()
    n_recent = n_early = 0
    for site, items in by_site.items():
        items.sort(key=lambda x: x[1])
        q = max(1, len(items) // 4)
        for ts, _ in items[:q]:
            recent.update(set(ts)); n_recent += 1
        for ts, _ in items[-q:]:
            early.update(set(ts)); n_early += 1
    drift = []
    for t in set(recent) | set(early):
        r, e = recent[t] / max(1, n_recent), early[t] / max(1, n_early)
        if recent[t] + early[t] >= 6:
            drift.append({"tag": t, "recent": round(r, 3), "early": round(e, 3), "delta": round(r - e, 3)})
    drift.sort(key=lambda d: -d["delta"])
    rising = [d for d in drift if d["delta"] > 0.03][:6]
    falling = [d for d in sorted(drift, key=lambda d: d["delta"]) if d["delta"] < -0.03][:6]

    # 口味分组：NMF 软主题（和推荐用的是同一套，见 themes.py）
    from .taste import background_works
    from .themes import Themes
    fav_works = [w for w, _ in favs]
    th = Themes(fav_works, background_works(db), exclude=set(hidden))
    clusters = [dict(c) for c in th.clusters]
    dist = (1.0 - th.fav_mix) if clusters else None       # 每本收藏离每种口味的远近
    for ci, c in enumerate(clusters):
        mem = c.pop("members")
        ac = Counter()
        for i in mem:
            for a in favs[i][0].authors:
                k = norm_person(a)
                if k in author_count:
                    ac[k] += 1
        c["authors"] = [author_name[k] for k, _ in ac.most_common(3)]
        c["name"] = "·".join(c["tags"][:2])
        # 代表作：离簇中心最近的几本
        rep_idx = sorted(mem, key=lambda i: dist[i, ci])[:4] if dist is not None else mem[:4]
        c["works"] = [_brief(favs[i][0]) for i in rep_idx]
        # 这一簇独有的味道：簇内比例 ÷ 全部收藏比例 最高的标签
        inside = Counter(t for i in mem for t in set(tag_sets[i]))
        m = max(1, len(mem))
        c["signature"] = [t for t, _ in sorted(((t, (k / m) / (tag_count[t] / n)) for t, k in inside.items()
                                                 if k / m >= 0.25), key=lambda x: -x[1])[:3]]
        c.pop("label", None)

    # 孤例：离所有口味簇都远、又带着你收藏里很少见的标签的那几本
    outliers = []
    if dist is not None and n >= 40:
        nearest = th.outlier_score(fav_works)          # 这几种口味解释不了它的程度
        rarity = []
        for i, ts in enumerate(tag_sets):
            rare = [t for t in ts if tag_count[t] <= 3 and not re.match(r"^(c\d{2,3}|comic.*|\d+.*)$", t)]
            rarity.append((nearest[i] + 0.08 * len(rare), rare))
        order = sorted(range(n), key=lambda i: -rarity[i][0])
        for i in order:
            if len(outliers) >= 6:
                break
            if not tag_sets[i]:
                continue
            outliers.append({**_brief(favs[i][0]), "rare_tags": rarity[i][1][:4]})
    lonely_tags = [t for t, c in tag_count.items() if c == 1]
    one_book_authors = sum(1 for c in author_count.values() if c == 1)

    # 调音里的本命
    honmei = []
    for r in db.q("SELECT c.site, c.wid FROM calibration c WHERE c.level = 4 ORDER BY c.at DESC LIMIT 40"):
        w = db.get_work(r["site"], r["wid"])
        if w:
            honmei.append(_brief(w))

    # 两站都收藏的同一作品
    keys = defaultdict(set)
    for w, _ in favs:
        k = title_key(w.title)
        if len(k) >= 4:
            keys[k].add(w.site)
    both = sum(1 for s in keys.values() if len(s) > 1)

    fb = Counter(r["label"] for r in db.q("SELECT label FROM feedback"))
    top_tags = [{"tag": t, "count": c, "share": round(c / n, 3)} for t, c in tag_count.most_common(30)]
    def _recent(ws: list[Work], k: int = 3) -> list[dict]:
        by_rank = {(w.site, w.wid): r for w, r in favs}
        return [_brief(w) for w in sorted(ws, key=lambda w: by_rank.get((w.site, w.wid), 1e9))[:k]]
    loyal = [{"name": author_name[k], "count": c, "works": _recent(author_works[k]) if i < 8 else []}
             for i, (k, c) in enumerate(author_count.most_common(10)) if c >= 2]
    # 封面墙：本命 → 各簇代表作 → 最新收藏
    mosaic, seen_m = [], set()
    pool = honmei + [b for c in clusters for b in c.get("works", [])] + [_brief(w) for w, r in sorted(favs, key=lambda x: x[1])[:24]]
    for b in pool:
        if (b["site"], b["wid"]) not in seen_m:
            seen_m.add((b["site"], b["wid"]))
            mosaic.append(b)
        if len(mosaic) >= 18:
            break

    return {
        "empty": False,
        "total": n,
        "per_site": dict(per_site),
        "distinct_authors": len(author_count),
        "distinct_parodies": len(parody_count),
        "repeat_authors": sum(1 for c in author_count.values() if c >= 2),
        "avg_tags": round(sum(len(t) for t in tag_sets) / n, 1),
        "color_share": round(color / n, 3),
        "long_share": round(long_ / n, 3),
        "both_sites": both,
        "top_tags": top_tags,
        "dimensions": dimensions,
        "axes": axes,
        "authors": loyal,
        "parodies": [{"name": parody_name[k], "count": c} for k, c in parody_count.most_common(8) if c >= 2],
        "groups": [{"name": g, "count": c} for g, c in group_count.most_common(5) if c >= 3],
        "rising": rising,
        "falling": falling,
        "clusters": clusters,
        "outliers": outliers,
        "lonely_tags": {"count": len(lonely_tags), "examples": lonely_tags[:12]},
        "one_book_authors": one_book_authors,
        "honmei": honmei,
        "mosaic": mosaic,
        "hidden_tags": sorted(hidden),
        "feedback": {"want": fb.get("want", 0), "skip": fb.get("skip", 0), "dislike": fb.get("dislike", 0), "seen": fb.get("seen", 0), "seen_like": fb.get("seen_like", 0)},
    }


def llm_digest(s: dict, db: DB | None = None) -> dict:
    """给大模型看的精简版：统计 + 一部分作品标题（调音里的本命 / 喜欢一定在内，再加各簇代表作、孤例、最新收藏）。"""
    if s.get("empty"):
        return s
    pct = lambda x: f"{round(x * 100)}%"
    fmt = lambda b: b["title"] + (f"（{'、'.join(b['authors'])}）" if b.get("authors") else "")
    works: dict = {}
    if db is not None:
        lv = {4: "本命", 3: "喜欢", 2: "一般", 1: "其实不太喜欢"}
        rated: dict[str, list[str]] = {}
        for r in db.q("SELECT site, wid, level, reasons FROM calibration WHERE level > 0 ORDER BY level DESC, at DESC"):
            w = db.get_work(r["site"], r["wid"])
            if w:
                why = "、".join(json.loads(r["reasons"] or "[]"))
                rated.setdefault(lv[r["level"]], []).append(fmt(_brief(w)) + (f" ——因为{why}" if why else ""))
        if rated:
            works["你在调音里亲自评过的"] = {k: v[:15] for k, v in rated.items()}
        newest = [fmt(_brief(w)) for w, rank in _favorites(db) if rank < 6]
        if newest:
            works["最新收藏的几本"] = newest
    works["各口味簇的代表作"] = {c["name"]: [fmt(b) for b in c.get("works", [])[:3]] for c in s["clusters"]}
    if s.get("outliers"):
        works["孤例（和主流口味都不像的几本）"] = [fmt(b) + (f"，带着 {'、'.join(b['rare_tags'])}" if b.get("rare_tags") else "")
                                         for b in s["outliers"]]
    return {
        "作品举例": works,
        "收藏总数": s["total"], "各站": s["per_site"],
        "作者数": s["distinct_authors"], "收藏过2本以上的作者数": s["repeat_authors"],
        "全彩占比": pct(s["color_share"]), "长篇占比": pct(s["long_share"]),
        "最常见标签(占收藏比例)": {t["tag"]: pct(t["share"]) for t in s["top_tags"][:25]},
        "各维度最常见标签": {d["name"]: [f"{t['tag']} {pct(t['share'])}" for t in d["top"][:6]] for d in s["dimensions"]},
        "倾向指数(命中比例)": {k: pct(v) for k, v in s["axes"].items()},
        "本命作者(收藏本数)": {a["name"]: a["count"] for a in s["authors"][:8]},
        "常见原作": {p["name"]: p["count"] for p in s["parodies"][:6]},
        "口味簇": [{"占比": pct(c["share"]), "代表标签": c["tags"], "代表作者": c["authors"]} for c in s["clusters"]],
        "近期上升的标签": [f"{d['tag']}（早期{pct(d['early'])}→近期{pct(d['recent'])}）" for d in s["rising"]],
        "近期下降的标签": [f"{d['tag']}（早期{pct(d['early'])}→近期{pct(d['recent'])}）" for d in s["falling"]],
        "在推荐里的表态": s["feedback"],
        "只收过一本的作者数": s.get("one_book_authors"),
        "只出现过一次的标签数": (s.get("lonely_tags") or {}).get("count"),
    }


def search_favorites(db: DB, q: str, limit: int = 20) -> list[dict]:
    """在收藏里按标题 / 作者找，给“手动添加本命”用。"""
    q = norm(q or "")
    if not q:
        return []
    honmei = {(r["site"], r["wid"]) for r in db.q("SELECT site, wid FROM calibration WHERE level = 4")}
    out = []
    for site in ("jm", "pica"):
        for w in db.collection(site):
            hay = norm(w.title) + " " + " ".join(norm_person(a) for a in w.authors)
            if q in hay:
                out.append({**_brief(w), "honmei": (w.site, w.wid) in honmei})
                if len(out) >= limit:
                    return out
    return out

