"""候选特征与推荐依据。

每个候选得到一行特征（都缩放到 0~1）：
    tag     标签匹配：与最接近几本收藏的标签（含原作）相似度，常见标签权重低。
            用不对称的相似度：候选的标签被收藏覆盖了多少，收藏多出来的标签只轻罚，
            这样标签多的哔咔收藏不会因为“标签太多”而显得和禁漫候选不像
    fit     口味契合：候选每个标签的偏爱度（见 taste.py，提升度 + 心动/略过）合起来，0.5 为一般水平
    author  作者亲和：作者/社团在你收藏里出现的次数（越新的收藏权重越高）
    title   标题相似：核心标题的字面相似（续作、同系列）
    semantic 语义相似：多语言 MiniLM 向量，与最接近几本收藏的相似度（关闭时为 0）
    neg     负例相似：与你点过“不感兴趣”的作品的相似度
    pop     热度：站内观看/喜欢数（对数缩放）
用“最接近的几本收藏”而不是全部收藏的平均，少数几本代表的小众口味也能单独命中。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .models import Work
from .normalize import norm, norm_parody, norm_person, norm_tags, parse_title

FEATURES = ["tag", "fit", "author", "title", "semantic", "neg", "pop"]
TVERSKY_A = 0.5    # 候选有、收藏没有的标签，罚得重一些
TVERSKY_B = 0.2    # 收藏有、候选没有的标签，轻罚
TOPK = 3
CROSS_SITE = True  # 跨站比较（哔咔收藏 vs 禁漫候选）只看两站都用的标签


def work_tokens(w: Work) -> list[str]:
    toks = norm_tags(w.tags)
    toks += ["原作:" + norm(norm_parody(p)) for p in w.parodies if norm(p)]
    return toks


def work_people(w: Work) -> list[tuple[str, float, str]]:
    """(规范化名字, 权重, 原名)。作者 1.0，社团 0.6。"""
    out = [(norm_person(a), 1.0, a) for a in w.authors if norm_person(a)]
    out += [(norm_person(c), 0.6, c) for c in w.circles if norm_person(c)]
    return out


def display_tag(t: str) -> str:
    return t.replace("原作:", "原作 ")


@dataclass
class Profile:
    favorites: list[Work]
    fav_weights: np.ndarray
    negatives: list[Work] = field(default_factory=list)

    @classmethod
    def build(cls, favorites_by_site: dict[str, list[Work]], negatives: list[Work]) -> "Profile":
        favs, weights = [], []
        for site, works in favorites_by_site.items():
            for rank, w in enumerate(works):
                favs.append(w)
                # 越新的收藏权重越高，但旧收藏仍保留至少一半权重
                weights.append(0.5 + 0.5 * math.exp(-rank / 200))
        return cls(favs, np.array(weights, dtype=float), negatives)

    @property
    def empty(self) -> bool:
        return not self.favorites


def _tfidf_matrix(docs: list[list[str]], vocab: dict[str, int], idf: np.ndarray) -> np.ndarray:
    m = np.zeros((len(docs), len(vocab)), dtype=np.float32)
    for i, toks in enumerate(docs):
        for t in toks:
            j = vocab.get(t)
            if j is not None:
                m[i, j] = idf[j]
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1
    return m / n


def _binary(docs: list[list[str]], vocab: dict[str, int]) -> np.ndarray:
    m = np.zeros((len(docs), len(vocab)), dtype=np.float32)
    for i, toks in enumerate(docs):
        for t in toks:
            j = vocab.get(t)
            if j is not None:
                m[i, j] = 1.0
    return m


def tversky(c_bin: np.ndarray, f_bin: np.ndarray, v: np.ndarray, a: float = TVERSKY_A, b: float = TVERSKY_B) -> np.ndarray:
    """加权 Tversky 相似度：共同 / (共同 + a·候选独有 + b·收藏独有)。"""
    if c_bin.shape[0] == 0 or f_bin.shape[0] == 0:
        return np.zeros((c_bin.shape[0], f_bin.shape[0]))
    inter = (c_bin * v[None, :]) @ f_bin.T
    cm = (c_bin @ v)[:, None]
    fm = (f_bin @ v)[None, :]
    den = inter + a * (cm - inter) + b * (fm - inter)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(den > 0, inter / den, 0.0)
    return out


def _topk_mean(sim: np.ndarray, weights: np.ndarray | None, k: int = TOPK) -> tuple[np.ndarray, np.ndarray]:
    """每行取最高的 k 个相似度求均值。返回 (均值, 最高者下标排序)。"""
    if sim.shape[1] == 0:
        return np.zeros(sim.shape[0]), np.zeros((sim.shape[0], 0), dtype=int)
    s = sim * weights[None, :] if weights is not None else sim
    k = min(k, s.shape[1])
    idx = np.argsort(-s, axis=1)[:, :k]
    vals = np.take_along_axis(s, idx, axis=1)
    return vals.mean(axis=1), idx


class FeatureBuilder:
    def __init__(self, profile: Profile, background: list[Work], semantic=None, taste=None,
                 hints: dict[str, list[str]] | None = None, mode: str = "v2"):
        """background：用于估计标签常见程度的作品。
        taste：口味模型（taste.TasteModel），有基准样本时标签常见度改用基准样本估计。
        hints：列表页还没有标签的候选，按“从哪个标签搜到的”补一点已知标签（只用于初筛）。
        mode：v1 = 旧版（余弦相似、无口味契合），留着做对比评测。"""
        self.p = profile
        self.semantic = semantic
        self.taste = taste if mode == "v2" else None
        self.hints = hints or {}
        self.mode = mode
        if self.taste is not None and self.taste.has_background:
            background = self.taste.background
        docs = [work_tokens(w) for w in background] + [work_tokens(w) for w in profile.favorites]
        df: dict[str, int] = {}
        for toks in docs:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        self.vocab = {t: i for i, t in enumerate(sorted(df))}
        n = max(1, len(docs))
        self.idf = np.array([math.log((n + 1) / (df[t] + 1)) + 1 for t in sorted(df)], dtype=np.float32)
        self.fav_tok = _tfidf_matrix([work_tokens(w) for w in profile.favorites], self.vocab, self.idf)
        self.fav_bin = _binary([work_tokens(w) for w in profile.favorites], self.vocab)
        # 两站共有的标签：只在一个站出现的标签，另一站的作品永远对不上，不该算进“独有”去罚分
        jm_docs = [work_tokens(w) for w in background] + [work_tokens(w) for w in profile.favorites if w.site == "jm"]
        jm_set = {t for d in jm_docs for t in d}
        self.cross = np.array([1.0 if t in jm_set else 0.0 for t in sorted(df)], dtype=np.float32)
        self.fav_cross = np.array([w.site != "jm" for w in profile.favorites])
        self.neg_tok = _tfidf_matrix([work_tokens(w) for w in profile.negatives], self.vocab, self.idf)
        # 作者亲和度
        self.people: dict[str, float] = {}
        self.people_name: dict[str, str] = {}
        for w, wt in zip(profile.favorites, profile.fav_weights):
            for k, pw, raw in work_people(w):
                self.people[k] = self.people.get(k, 0.0) + pw * wt
                self.people_name.setdefault(k, raw)
        self.neg_people = {k for w in profile.negatives for k, _, _ in work_people(w)}
        # 标签在收藏里的加权出现率，用于挑“共同标签”
        self.tag_pref: dict[str, float] = {}
        for w, wt in zip(profile.favorites, profile.fav_weights):
            for t in set(work_tokens(w)):
                self.tag_pref[t] = self.tag_pref.get(t, 0.0) + wt

    # ---- 标题字面相似（字符 n-gram） ----
    def _title_sim(self, cands: list[Work], refs: list[Work]) -> np.ndarray:
        if not cands or not refs:
            return np.zeros((len(cands), len(refs)))
        from sklearn.feature_extraction.text import TfidfVectorizer
        texts = [norm(parse_title(w.title).core) or norm(w.title) for w in cands + refs]
        try:
            vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 3), min_df=1).fit(texts)
        except ValueError:
            return np.zeros((len(cands), len(refs)))
        a = vec.transform(texts[: len(cands)])
        b = vec.transform(texts[len(cands):])
        return (a @ b.T).toarray()

    def _tokens(self, w: Work) -> list[str]:
        toks = work_tokens(w)
        extra = self.hints.get(w.wid) if not w.detail_ok else None
        return toks + [t for t in (extra or []) if t not in toks]

    def build(self, cands: list[Work]) -> tuple[list[dict], list[dict]]:
        """返回 (特征行, 推荐依据)，与 cands 一一对应。"""
        if not cands:
            return [], []
        favs = self.p.favorites
        c_docs = [self._tokens(w) for w in cands]
        c_tok = _tfidf_matrix(c_docs, self.vocab, self.idf)
        if not favs:
            tag_sim = np.zeros((len(cands), 0))
        elif self.mode == "v1":
            tag_sim = c_tok @ self.fav_tok.T
        else:
            c_bin = _binary(c_docs, self.vocab)
            tag_sim = tversky(c_bin, self.fav_bin, self.idf ** 2)
            if CROSS_SITE and self.fav_cross.any():
                fb = self.fav_bin[self.fav_cross]
                shared = (fb.max(axis=0) > 0).astype(np.float32) * self.cross   # 哔咔收藏里出现过、禁漫也用的标签
                tag_sim[:, self.fav_cross] = tversky(c_bin * shared[None, :], fb * shared[None, :], self.idf ** 2)
        tag_feat, _ = _topk_mean(tag_sim, None)
        fit_feat = np.array([self.taste.fit(w, self.hints.get(w.wid) if not w.detail_ok else None)
                             for w in cands]) if self.taste is not None else np.zeros(len(cands))

        title_sim = self._title_sim(cands, favs)
        title_feat, _ = _topk_mean(title_sim, None, k=1)

        sem_sim = np.zeros((len(cands), len(favs)))
        sem_feat = np.zeros(len(cands))
        if self.semantic is not None and favs:
            sem_sim = self.semantic.similarity(cands, favs)
            sem_feat, _ = _topk_mean(sem_sim, None)

        # 负例
        neg_feat = np.zeros(len(cands))
        if self.p.negatives:
            nt = (c_tok @ self.neg_tok.T).max(axis=1)
            if self.semantic is not None:
                ns = self.semantic.similarity(cands, self.p.negatives).max(axis=1)
                nt = np.maximum(nt, ns)
            same_author = np.array([
                1.0 if any(k in self.neg_people for k, _, _ in work_people(w)) else 0.0 for w in cands
            ])
            neg_feat = np.clip(0.7 * nt + 0.3 * same_author, 0, 1)

        pops = np.array([math.log1p(max(0.0, w.popularity)) for w in cands])
        span = pops.max() - pops.min()
        pop_feat = (pops - pops.min()) / span if span > 0 else np.zeros(len(cands))

        # 合并相似度，用来找“最接近的收藏”
        combined = tag_sim + 0.5 * title_sim + (sem_sim if self.semantic is not None else 0)
        rows, reasons = [], []
        for i, w in enumerate(cands):
            best_aff, best_name, best_kind = 0.0, "", "作者"
            for k, pw, _ in work_people(w):
                v = self.people.get(k, 0.0) * pw
                if v > best_aff:
                    best_aff, best_name = v, self.people_name.get(k, k)
                    best_kind = "作者" if pw == 1.0 else "社团"
            author_feat = 1 - math.exp(-best_aff / 1.5)
            rows.append({
                "tag": round(float(tag_feat[i]), 4),
                "fit": round(float(fit_feat[i]), 4),
                "author": round(author_feat, 4),
                "title": round(float(title_feat[i]), 4),
                "semantic": round(float(sem_feat[i]), 4),
                "neg": round(float(neg_feat[i]), 4),
                "pop": round(float(pop_feat[i]), 4),
            })
            reasons.append(self._reason(w, i, combined, best_name, best_aff))
            reasons[-1]["author_kind"] = best_kind
        return rows, reasons

    def _reason(self, w: Work, i: int, combined, author_name: str, author_aff: float) -> dict:
        favs = self.p.favorites
        nearest = []
        if favs:
            order = np.argsort(-combined[i])[:TOPK]
            for j in order:
                if combined[i, j] < 0.15:
                    continue
                f = favs[j]
                nearest.append({"site": f.site, "wid": f.wid, "title": parse_title(f.title).core or f.title,
                                "url": f.url, "sim": round(float(combined[i, j]), 3)})
        toks = set(self._tokens(w))
        if self.taste is not None:
            liked = [t for t in toks if self.taste.pref.get(t, 0) > 0.15 and t in self.tag_pref]
            shared = sorted(liked, key=lambda t: -self.taste.pref[t])[:4]
            return {
                "author": author_name if author_aff > 0 else "",
                "author_count": round(author_aff, 1),
                "shared_tags": [display_tag(t) for t in shared],
                "nearest": nearest,
            }
        shared = sorted(
            (t for t in toks if t in self.tag_pref),
            key=lambda t: -(self.tag_pref[t] * self.idf[self.vocab[t]] if t in self.vocab else 0),
        )[:4]
        return {
            "author": author_name if author_aff > 0 else "",
            "author_count": round(author_aff, 1),
            "shared_tags": [display_tag(t) for t in shared],
            "nearest": nearest,
        }
