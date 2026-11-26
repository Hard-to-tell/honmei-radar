"""口味主题：把收藏拆成几种“味道”（NMF），一本书可以同时带几种味道。

和以前的 K-means 比：
  - 软归属：一本书可以是“六成纯爱 + 四成姐姐”，不用硬塞进一个簇；
  - 结果确定：同样的收藏每次算出来都一样；
  - 先洗标签：长篇 / 短篇 / 同人 这类格式标签不参与；只在一个站出现的标签也不参与
    （否则分出来的是“哔咔的书 / 禁漫的书”，不是口味）；每个站各自算标签的稀有程度。
"""
from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np

from .features import work_tokens
from .models import Work

# 格式、篇幅、来源这类标签：说明“是什么形式”，不说明“是什么口味”
FORMAT_TAGS = {
    "长篇", "短篇", "短篇合集", "中篇", "同人", "全彩", "彩色", "黑白", "cg杂图", "cg", "连载", "连载中", "完结",
    "单行本", "杂志", "webtoon", "生肉", "英语 eng", "english", "汉化", "无修正", "有修正", "dl版", "图集",
}
_EVENT = re.compile(r"^(c\d{2,3}|comic\d.*|comic1.*|\d+.*)$")

MIN_DF = 3            # 收藏里至少出现几次才算一个可用的标签
MAX_DF = 0.5          # 一半以上收藏都有的标签分不出口味
SITE_MIN_RATE = 0.01   # 两站都有收藏时，标签在每个站至少要出现这么多比例
K_MIN, K_MAX = 3, 8
MIN_SHARE = 0.03      # 一种味道至少占这么多
MAX_OVERLAP = 0.5     # 两种味道的标签构成太像就算重复


def theme_tokens(w: Work, exclude: set[str] | None = None) -> set[str]:
    return {t for t in work_tokens(w)
            if not t.startswith("原作:") and t not in FORMAT_TAGS and not _EVENT.match(t)
            and not (exclude and t in exclude)}


class Themes:
    def __init__(self, works: list[Work], background: list[Work] | None = None,
                 exclude: set[str] | None = None, k: int | None = None):
        self.exclude = exclude or set()
        self.works = works
        self.clusters: list[dict] = []
        self.nmf = None
        self.vocab: dict[str, int] = {}
        n = len(works)
        if n < 20:
            return
        docs = [theme_tokens(w, self.exclude) for w in works]
        sites = [w.site for w in works]

        # 1) 可用的标签
        df = Counter(t for d in docs for t in d)
        site_n = Counter(sites)
        site_df: dict[str, Counter] = {s: Counter() for s in site_n}
        for d, s in zip(docs, sites):
            site_df[s].update(d)
        bg_df: Counter = Counter()
        bg_site: Counter = Counter()
        for w in background or []:
            bg_site[w.site] += 1
            for t in theme_tokens(w, self.exclude):
                bg_df[(w.site, t)] += 1
        both = [s for s, c in site_n.items() if c >= 20]

        def usable(t: str) -> bool:
            if df[t] < MIN_DF or df[t] / n > MAX_DF:
                return False
            if len(both) >= 2:   # 两站都有收藏：只在一个站出现的标签不参与分味道
                for s in both:
                    rate = (site_df[s][t] + bg_df[(s, t)]) / (site_n[s] + bg_site[s])
                    if rate < SITE_MIN_RATE:
                        return False
            return True
        vocab = sorted(t for t in df if usable(t))
        if len(vocab) < 5:
            return
        self.vocab = {t: i for i, t in enumerate(vocab)}
        # 2) 每站各自的稀有程度（同一个标签在哔咔很常见、在禁漫少见，分量就不同）
        self.idf: dict[str, np.ndarray] = {}
        for s, cnt in site_n.items():
            self.idf[s] = np.array([math.log((cnt + 1) / (site_df[s][t] + 1)) + 1 for t in vocab], dtype=np.float32)
        self.default_idf = np.array([math.log((n + 1) / (df[t] + 1)) + 1 for t in vocab], dtype=np.float32)
        X = self._matrix(docs, sites)
        keep = X.sum(axis=1) > 0
        if keep.sum() < 20:
            return
        # 3) 选几种味道：从多往少试，取“每种都够大、彼此不重复”的最多的那个
        from sklearn.decomposition import NMF
        best = None
        ks = [k] if k else range(min(K_MAX, max(K_MIN, int(keep.sum()) // 25)), K_MIN - 1, -1)
        for kk in ks:
            model = NMF(n_components=kk, init="nndsvda", max_iter=600, random_state=7)
            W = model.fit_transform(X[keep])
            H = model.components_
            mix = _rownorm(W)
            share = mix.mean(axis=0)
            Hn = H / (np.linalg.norm(H, axis=1, keepdims=True) + 1e-9)
            overlap = (Hn @ Hn.T - np.eye(kk)).max() if kk > 1 else 0.0
            best = (model, kk)
            if share.min() >= MIN_SHARE and overlap < MAX_OVERLAP:
                break
        self.nmf, self.k = best
        mix = self.mixture_of_docs(docs, sites)
        self.fav_mix = mix
        label = np.where(mix.sum(axis=1) > 0, mix.argmax(axis=1), -1)
        inv = np.array(vocab)
        out = []
        for c in range(self.k):
            members = np.where(label == c)[0].tolist()
            h = self.nmf.components_[c]
            out.append({"label": c, "size": len(members), "share": round(float(mix[:, c].mean()), 3),
                        "tags": [str(inv[i]) for i in np.argsort(-h)[:6] if h[i] > 0],
                        "members": members})
        order = sorted(range(self.k), key=lambda c: -out[c]["share"])
        self.order = order                       # 第 i 个口味 = nmf 的第 order[i] 个分量
        self.clusters = [out[c] for c in order]

    # ---- 工具 ----
    def _matrix(self, docs: list[set[str]], sites: list[str]) -> np.ndarray:
        X = np.zeros((len(docs), len(self.vocab)), dtype=np.float32)
        for i, (d, s) in enumerate(zip(docs, sites)):
            idf = self.idf.get(s, self.default_idf)
            for t in d:
                j = self.vocab.get(t)
                if j is not None:
                    X[i, j] = idf[j]
        return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)

    def mixture_of_docs(self, docs: list[set[str]], sites: list[str]) -> np.ndarray:
        """每本书在各种味道上的占比（每行加起来是 1；一个可用标签都没有就全是 0）。列按 clusters 的次序。"""
        if self.nmf is None:
            return np.zeros((len(docs), 0))
        X = self._matrix(docs, sites)
        W = self.nmf.transform(X)
        mix = _rownorm(W)
        return mix[:, self.order] if hasattr(self, "order") else mix

    def mixture(self, works: list[Work]) -> np.ndarray:
        return self.mixture_of_docs([theme_tokens(w, self.exclude) for w in works], [w.site for w in works])

    def assign(self, works: list[Work]) -> list[int]:
        """每本书最主要的那种味道（列在 clusters 里的下标），对不上任何味道是 -1。"""
        if self.nmf is None or not works:
            return [-1] * len(works)
        mix = self.mixture(works)
        return [int(r.argmax()) if r.sum() > 0 else -1 for r in mix]

    def distances(self, works: list[Work]) -> np.ndarray | None:
        """离每种味道的“远近”（0 最近，1 最远），和以前簇中心距离的用法一样。"""
        if self.nmf is None or not works:
            return None
        return 1.0 - self.mixture(works)

    def outlier_score(self, works: list[Work]) -> np.ndarray:
        """这几种味道解释不了它的程度（0~1）：重建出来的标签和原标签越不像，越像孤例。"""
        if self.nmf is None:
            return np.ones(len(works))
        docs = [theme_tokens(w, self.exclude) for w in works]
        X = self._matrix(docs, [w.site for w in works])
        R = self.nmf.transform(X) @ self.nmf.components_
        cos = (X * R).sum(axis=1) / (np.linalg.norm(R, axis=1) + 1e-9)
        return np.clip(1.0 - cos, 0.0, 1.0)


def _rownorm(W: np.ndarray) -> np.ndarray:
    s = W.sum(axis=1, keepdims=True)
    return np.divide(W, s, out=np.zeros_like(W), where=s > 0)
