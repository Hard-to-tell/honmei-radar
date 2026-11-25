"""站点适配器接口。新站点只需实现这些方法。"""
from __future__ import annotations

import threading
import time
from typing import Callable, Iterable

from ..models import Work
from ..normalize import parse_title

Progress = Callable[[str], None]


class SiteError(Exception):
    """给用户看的错误（登录失败、网络不通等）。"""


class Throttle:
    """同一站点的请求最小间隔，避免请求过快被限流。"""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            gap = self._last + self.interval - time.monotonic()
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()


def enrich_from_title(w: Work) -> Work:
    """把标题里的作者、社团、原作、标记补进字段（不覆盖已有值）。"""
    pt = parse_title(w.title)

    def add(dst: list[str], items: Iterable[str]) -> None:
        low = {x.lower() for x in dst}
        for it in items:
            if it and it.lower() not in low:
                dst.append(it)
                low.add(it.lower())

    add(w.authors, pt.artists)
    add(w.circles, pt.circles)
    add(w.parodies, pt.parodies)
    add(w.groups, pt.groups)
    add(w.tags, pt.flags)
    return w


class SiteAdapter:
    site: str = ""
    name: str = ""

    # ---- 登录 ----
    def login(self, username: str, password: str) -> str:
        raise NotImplementedError

    def logged_in(self) -> bool:
        raise NotImplementedError

    # ---- 收藏（按收藏时间从新到旧） ----
    def fetch_favorites(self, progress: Progress | None = None, max_pages: int | None = None) -> list[Work]:
        raise NotImplementedError

    # ---- 详情（标签、简介） ----
    def fetch_detail(self, wid: str) -> tuple[Work, list[Work]]:
        """返回 (详情作品, 相关作品列表)。"""
        raise NotImplementedError

    # ---- 候选检索（目前只有禁漫需要） ----
    def search(self, query: str, mode: str = "site", page: int = 1, order: str = "latest") -> list[Work]:
        raise NotImplementedError

    def ranking(self, kind: str = "week", page: int = 1) -> list[Work]:
        raise NotImplementedError

    def browse(self, page: int = 1, category: str = "all", order: str = "latest") -> list[Work]:
        """按分类浏览（默认全部、最新更新）。用来取一份不带口味偏向的站内样本。"""
        raise NotImplementedError

    def web_url(self, wid: str) -> str:
        raise NotImplementedError
