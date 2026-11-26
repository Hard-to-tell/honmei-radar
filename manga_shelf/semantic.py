"""可选的语义匹配：冻结的多语言 MiniLM，本地推理，向量缓存在数据库里。

默认关闭。打开后第一次使用会下载模型（约 470MB），之后离线可用。
"""
from __future__ import annotations

import hashlib
import os

import numpy as np

from .db import DB
from .models import Work
from .normalize import parse_title, to_simplified

_MODEL_CACHE: dict = {}


class SemanticUnavailable(Exception):
    pass


def work_text(w: Work) -> str:
    pt = parse_title(w.title)
    parts = [pt.core or w.title]
    if w.parodies:
        parts.append("原作：" + "、".join(w.parodies))
    if w.tags:
        parts.append("标签：" + "、".join(w.tags[:15]))
    # 简介不用：两站的简介大多是乱写的，只会把向量带偏
    return to_simplified("。".join(parts))


class Semantic:
    def __init__(self, db: DB, model_name: str, hf_endpoint: str = "", proxy: str | None = None):
        self.db = db
        self.model_name = model_name
        if hf_endpoint:
            os.environ["HF_ENDPOINT"] = hf_endpoint
        if proxy:
            os.environ["HTTPS_PROXY"] = proxy
            os.environ["HTTP_PROXY"] = proxy
        self._model = None

    def model(self):
        if self._model is None:
            if self.model_name in _MODEL_CACHE:
                self._model = _MODEL_CACHE[self.model_name]
            else:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as e:
                    raise SemanticUnavailable(
                        "语义匹配组件还没安装：在“账号与设置”里点“安装语义组件”"
                    ) from e
                try:
                    self._model = SentenceTransformer(self.model_name, device="cpu")
                except Exception as e:
                    raise SemanticUnavailable(f"模型下载或加载失败：{e}（可在设置里点“重新检测网络”后再试）") from e
                _MODEL_CACHE[self.model_name] = self._model
        return self._model

    def vectors(self, works: list[Work]) -> np.ndarray:
        texts = [work_text(w) for w in works]
        hashes = [hashlib.md5(t.encode("utf-8")).hexdigest() for t in texts]
        out: list[np.ndarray | None] = [None] * len(works)
        missing = []
        for i, w in enumerate(works):
            r = self.db.q("SELECT text_hash, vec FROM embeddings WHERE site=? AND wid=? AND model=?",
                          (w.site, w.wid, self.model_name))
            if r and r[0]["text_hash"] == hashes[i]:
                out[i] = np.frombuffer(r[0]["vec"], dtype=np.float32)
            else:
                missing.append(i)
        if missing:
            vecs = self.model().encode([texts[i] for i in missing], batch_size=32,
                                       normalize_embeddings=True, show_progress_bar=False)
            for i, v in zip(missing, vecs):
                v = np.asarray(v, dtype=np.float32)
                out[i] = v
                w = works[i]
                self.db.x("INSERT OR REPLACE INTO embeddings(site, wid, model, text_hash, vec) VALUES (?,?,?,?,?)",
                          (w.site, w.wid, self.model_name, hashes[i], v.tobytes()))
        return np.vstack(out) if out else np.zeros((0, 384), dtype=np.float32)

    def similarity(self, a: list[Work], b: list[Work]) -> np.ndarray:
        if not a or not b:
            return np.zeros((len(a), len(b)))
        va, vb = self.vectors(a), self.vectors(b)
        return np.clip(va @ vb.T, 0, 1)


def get_semantic(db: DB, settings: dict) -> Semantic | None:
    s = settings.get("semantic") or {}
    if not s.get("enabled"):
        return None
    from .net import effective_proxy, hf_endpoint
    return Semantic(db, s.get("model"), hf_endpoint(settings), effective_proxy(settings))
