"""检验算法：v0.3（旧）和 v0.4（新）谁更懂你。

v0.4 = 标题设定词补标签 + 两站标签对齐 + NMF 软口味 + MMR 组卷；v0.3 = K-means 硬分组 + 按簇名额。

做法（时间切分，模拟“用过去猜未来”）：
  - 藏起最新 20% 的禁漫收藏当“答案”；最新 20% 的哔咔收藏里，能按标题在禁漫找到同一本的也算答案；
  - 只用其余收藏建立口味；
  - 负例：检索出来但你没收藏的禁漫作品（已取过详情的）+ 站内随机样本（最新更新、随机分类页）；
    榜单作品不当负例（太好分辨）；藏起来的收藏的“相邻作品”也不当负例（等于提前看答案）；
  - 每个版本给答案和负例一起打分、按各自的规则挑出一卷 20 本，看命中几本。重复几次取平均。
负例只是“没收藏”，不一定不喜欢，所以数字只用来比较两个版本的相对好坏。
"""
from __future__ import annotations

import random
import time
from collections import Counter

import numpy as np

from . import normalize
from .db import DB
from .features import FeatureBuilder, Profile
from .models import Work
from .normalize import title_key
from .ranker import BASE_WEIGHTS, V1_WEIGHTS, _auc, base_score
from .taste import BG_KEY, TasteModel, background_works, background_stale, learn_aliases, refresh_background

HOLDOUT = 0.2
REPEATS = 3
TOP = 20

VARIANTS = {"v0.3": "v0.3（旧版）", "v0.4": "v0.4（新算法）"}


class EvalError(Exception):
    pass


def _without_learned_aliases(fn):
    """v0.2 没有跨站对照，评测它时临时关掉学到的对照。"""
    normalize.USE_LEARNED = False
    normalize.reset_aliases()
    try:
        return fn()
    finally:
        normalize.USE_LEARNED = True
        normalize.reset_aliases()


def _slate(cands, scores, k, clusters=None, cinfo=None, seed=0, div=None):
    from .recommend import _pick
    rnd = random.Random(seed)
    return _pick(cands, list(scores), ["eval"] * len(cands), k, 0, 2, rnd, clusters, cinfo, div=div)


def _title_hints(on: bool) -> None:
    normalize.USE_TITLE_HINTS = on
    normalize.title_tags.cache_clear()


def evaluate(db: DB, jm=None, progress=None, seed: int = 7) -> dict:
    if jm is not None and background_stale(db):
        refresh_background(db, jm, progress)
    learn_aliases(db)
    jm_all = db.collection("jm")
    jm_favs = [w for w in jm_all if w.detail_ok]
    pica_favs = db.collection("pica")
    if len(jm_favs) < 25:
        raise EvalError("禁漫收藏少于 25 本（含详情），评测不出什么，先多收藏一些。")

    # 答案：最新 20% 禁漫收藏 + 最新 20% 哔咔收藏在禁漫的同一本
    n_hold = max(10, int(len(jm_favs) * HOLDOUT))
    held, train_jm = jm_favs[:n_hold], jm_favs[n_hold:]
    n_hold_p = int(len(pica_favs) * HOLDOUT)
    held_p, train_pica = pica_favs[:n_hold_p], pica_favs[n_hold_p:]
    train_keys = {title_key(w.title) for w in train_pica + train_jm if len(title_key(w.title)) >= 4}
    positives = [w for w in held if title_key(w.title) not in train_keys]
    hp_keys = {title_key(w.title) for w in held_p if len(title_key(w.title)) >= 4} - train_keys
    fav_ids = {w.wid for w in jm_all}
    pos_ids = {w.wid for w in positives}
    for r in db.q("SELECT * FROM works WHERE site='jm' AND detail_ok=1"):
        w = db._row_to_work(r)
        if w.wid not in fav_ids and w.wid not in pos_ids and title_key(w.title) in hp_keys:
            positives.append(w)
            pos_ids.add(w.wid)
    # 你在推荐里点过“心动”或“读过·喜欢”的也算答案；点过任何反馈的都不拿来当负例（读过没收藏不等于不喜欢）
    fb = db.feedback_map()
    for (site, wid), f in fb.items():
        if site == "jm" and f["label"] in ("want", "seen_like") and wid not in fav_ids and wid not in pos_ids:
            w = db.get_work(site, wid)
            if w and w.detail_ok:
                positives.append(w)
                pos_ids.add(wid)
    fb_ids = {wid for (site, wid) in fb if site == "jm"}
    if len(positives) < 8:
        raise EvalError("能用来当答案的新收藏太少（不到 8 本）。")

    # 负例
    leak = set()
    for w in held:
        leak.update(db.kv_get(f"related:jm:{w.wid}") or [])
    all_keys = {title_key(w.title) for w in pica_favs + jm_all if len(title_key(w.title)) >= 4}
    src = (db.kv_get(BG_KEY) or {}).get("src") or {}

    def ok_neg(w: Work) -> bool:
        return (w.wid not in fav_ids and w.wid not in pos_ids and w.wid not in leak and w.wid not in fb_ids
                and title_key(w.title) not in all_keys)
    rand_bg = [w for w in background_works(db, ("latest", "random")) if ok_neg(w)]
    rank_bg = background_works(db, ("rank",))
    retrieved = [db._row_to_work(r) for r in db.q("SELECT * FROM works WHERE site='jm' AND detail_ok=1")]
    retrieved = [w for w in retrieved if w.wid not in src and ok_neg(w)]
    if len(rand_bg) + len(retrieved) < 40:
        raise EvalError("负例不够：需要联网取一次站内样本，或者先开几卷让检索结果攒起来。")

    profile = Profile.build({"pica": train_pica, "jm": train_jm}, [])
    rnd = random.Random(seed)
    runs: dict[str, list[dict]] = {k: [] for k in VARIANTS}
    for rep in range(REPEATS):
        if progress:
            progress(f"第 {rep + 1}/{REPEATS} 轮对比", rep + 1, REPEATS)
        sample = rand_bg[:]
        rnd.shuffle(sample)
        half = len(sample) // 2
        ref = rank_bg + sample[:half]          # 给口味模型当参照
        negs = sample[half:] + retrieved
        cands: list[Work] = positives + negs
        y = np.array([1] * len(positives) + [0] * len(negs))

        from .recommend import DIV_DEFAULT, _similarity
        # 旧版：不看标题设定词、K-means 硬分组、按簇名额
        _title_hints(False)
        try:
            t3 = TasteModel(profile.favorites, profile.fav_weights, ref, [], clusters=True, cluster_algo="kmeans")
            cl3 = t3.cluster_of(cands)
            f3, r3 = FeatureBuilder(profile, ref, None, taste=t3).build(cands)
        finally:
            _title_hints(True)
        s3 = [base_score(x, False, BASE_WEIGHTS) for x in f3]
        # 新版
        taste = TasteModel(profile.favorites, profile.fav_weights, ref, [], clusters=True)
        cl = taste.cluster_of(cands)
        f4, r4 = FeatureBuilder(profile, ref, None, taste=taste).build(cands)
        s4 = [base_score(x, False, BASE_WEIGHTS) for x in f4]
        sim = _similarity(cands)          # 同一把尺子量“一卷有多不重样”

        for name, scores, reasons, cls, cinfo, div in (("v0.3", s3, r3, cl3, t3.clusters, None),
                                                      ("v0.4", s4, r4, cl, taste.clusters, DIV_DEFAULT)):
            pick = _slate(cands, scores, TOP, cls, cinfo, rep, div)
            # 另比一次只混随机作品（不混检索出来的同作者 / 同标签作品）的，难度低一些、更看排序本身
            sub = list(range(len(positives))) + list(range(len(positives), len(positives) + len(sample) - half))
            pick_r = _slate([cands[i] for i in sub], [scores[i] for i in sub], TOP, [cls[i] for i in sub], cinfo, rep, div)
            hits = [p for p in pick if y[p] == 1]
            near = [reasons[p]["nearest"][0]["site"] for p in pick if reasons[p].get("nearest")]
            order = np.argsort(-np.array(scores), kind="stable")
            rank_of = {int(i): r for r, i in enumerate(order)}
            runs[name].append({
                "hits": len(hits),
                "hits_rand": sum(1 for p in pick_r if y[sub[p]] == 1),
                "hit_rank": float(np.mean([pick.index(p) + 1 for p in hits])) if hits else None,
                "pos_pct": float(np.mean([rank_of[i] / max(1, len(cands) - 1) for i in range(len(positives))])),
                "auc": _auc(y, np.array(scores)),
                "pica_near": (near.count("pica") / len(near)) if near else 0.0,
                "clusters": len({cl[p] for p in pick if cl[p] >= 0}),   # 都按新版的口味分组来数
                "variety": float(1 - np.mean([sim[a, b] for a in pick for b in pick if a < b])) if len(pick) > 1 else 0.0,
            })

    def avg(rs, k):
        v = [r[k] for r in rs if r[k] is not None]
        return round(float(np.mean(v)), 3) if v else None
    pica_share = len(train_pica) / max(1, len(train_pica) + len(train_jm))
    out = {"at": time.time(), "positives": len(positives), "negatives": len(negs), "top": TOP,
           "repeats": REPEATS, "pica_share": round(pica_share, 3),
           "n_clusters": len(taste.clusters), "variants": {}}
    for k, rs in runs.items():
        out["variants"][k] = {"name": VARIANTS[k], **{m: avg(rs, m) for m in
                              ("hits", "hits_rand", "hit_rank", "pos_pct", "auc", "pica_near", "clusters", "variety")}}
    a, b = out["variants"]["v0.3"], out["variants"]["v0.4"]
    out["summary"] = (f"{len(positives)} 本藏起来的新收藏混进 {len(negs)} 本没收藏、也没表过态的作品里，各挑一卷 {TOP} 本："
                      f"v0.4 命中 {b['hits']:.1f} 本（v0.3 {a['hits']:.1f}）；"
                      f"只混随机作品时命中 {b['hits_rand']:.1f} 本（v0.3 {a['hits_rand']:.1f}）；"
                      f"最接近的收藏来自哔咔 {b['pica_near']:.0%}（v0.3 {a['pica_near']:.0%}，你的收藏里哔咔占 {pica_share:.0%}）；"
                      f"一卷覆盖 {b['clusters']:.1f} 种口味（v0.3 {a['clusters']:.1f}，共 {len(taste.clusters)} 种），"
                      f"不重样程度 {b['variety']:.2f}（v0.3 {a['variety']:.2f}）。")
    db.kv_set("eval", out)
    return out
