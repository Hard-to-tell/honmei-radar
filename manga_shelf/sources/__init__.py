from __future__ import annotations

from ..config import load_settings
from ..db import DB
from .base import SiteAdapter, SiteError

SITES = {"jm": "禁漫", "pica": "哔咔"}


def get_adapter(site: str, db: DB) -> SiteAdapter:
    if load_settings().get("demo"):
        from .demo import DemoAdapter
        return DemoAdapter(site)
    if site == "jm":
        from .jm import JMAdapter
        return JMAdapter(db)
    if site == "pica":
        from .pica import PicaAdapter
        return PicaAdapter(db)
    raise SiteError(f"未知站点：{site}")


__all__ = ["get_adapter", "SiteAdapter", "SiteError", "SITES"]
