from __future__ import annotations

from dataclasses import dataclass, field, asdict


@dataclass
class Work:
    """两站作品的统一表示。原文字段保留，规范化在 normalize.py 里做。"""
    site: str                 # "jm" / "pica"
    wid: str                  # 平台 ID
    title: str
    authors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    parodies: list[str] = field(default_factory=list)   # 原作 / 作品
    circles: list[str] = field(default_factory=list)    # 社团
    groups: list[str] = field(default_factory=list)     # 汉化组
    category: str = ""
    description: str = ""
    cover: str = ""
    url: str = ""
    popularity: float = 0.0
    detail_ok: bool = False   # 是否已取过详情（标签、简介）

    @property
    def key(self) -> tuple[str, str]:
        return (self.site, self.wid)

    def to_dict(self) -> dict:
        return asdict(self)
