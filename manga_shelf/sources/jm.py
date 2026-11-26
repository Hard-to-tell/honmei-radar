"""禁漫适配器：基于开源库 jmcomic（移动端接口，自动切换可用域名）。"""
from __future__ import annotations

import re

from ..config import load_settings
from ..db import DB
from ..models import Work
from .base import Progress, SiteAdapter, SiteError, Throttle, enrich_from_title


def _num(s) -> float:
    """'1.2K' / '40K' / '1234' -> 数字"""
    if s is None:
        return 0.0
    s = str(s).strip().upper()
    m = re.match(r"^([\d.]+)\s*([KMW万]?)", s)
    if not m:
        return 0.0
    v = float(m.group(1))
    return v * {"K": 1e3, "M": 1e6, "W": 1e4, "万": 1e4}.get(m.group(2), 1)


_NET_WORDS = ("timeout", "timed out", "connect", "resolve", "ssl", "proxy", "域名", "network", "curl: (")


def _friendly_login_error(e: Exception) -> str:
    t = str(e)
    low = t.lower()
    if any(k in low for k in ("password", "密码", "帐号", "账号", "username", "用户名")):
        return "禁漫登录失败：用户名或密码不对，再检查一下。"
    if any(k in low for k in _NET_WORDS):
        return ("连不上禁漫。书架会自动换可用的线路，稍等一会儿再试；还不行的话，打开你平时用的加速器 / 代理，"
                "再到 设置 → 网络 点“重新检测”。")
    return f"禁漫登录失败：{t[:200]}"


class JMAdapter(SiteAdapter):
    site = "jm"
    name = "禁漫"

    def __init__(self, db: DB):
        self.db = db
        self.settings = load_settings()
        self.throttle = Throttle(float(self.settings.get("rate_interval", 0.15)))
        self._client = None
        self._logged = False

    # ---------- 客户端 ----------
    def _new_client(self):
        try:
            import jmcomic
            from jmcomic import JmModuleConfig
        except ImportError as e:  # pragma: no cover
            raise SiteError("缺少 jmcomic 库，请重新运行安装脚本") from e
        JmModuleConfig.disable_jm_log()
        meta = {"impersonate": "chrome", "headers": None, "proxies": None, "timeout": 15}
        from ..net import effective_proxy
        proxy = effective_proxy(self.settings)
        if proxy:
            meta["proxies"] = {"http": proxy, "https": proxy}
        opt = jmcomic.JmOption.construct({
            "log": False,
            "client": {
                "impl": "api",
                "domain": list(self.settings["jm"].get("api_domains") or []),
                "retry_times": 2,
                "postman": {"type": "curl_cffi", "meta_data": meta},
            },
        })
        return opt.new_jm_client()

    def client(self):
        if self._client is None:
            self._client = self._new_client()
        if not self._logged:
            u = self.db.secret_get("jm", "username")
            p = self.db.secret_get("jm", "password")
            if u and p:
                self._do_login(u, p)
        return self._client

    def _do_login(self, username: str, password: str) -> None:
        self.throttle.wait()
        try:
            self._client.login(username, password)
        except Exception as e:
            raise SiteError(_friendly_login_error(e)) from e
        self._logged = True

    def login(self, username: str, password: str) -> str:
        if self._client is None:
            self._client = self._new_client()
        self._do_login(username, password)
        self.db.secret_set("jm", "username", username)
        self.db.secret_set("jm", "password", password)
        return "ok"

    def logged_in(self) -> bool:
        return bool(self.db.secret_get("jm", "username"))

    # ---------- 转换 ----------
    def web_url(self, wid: str) -> str:
        from ..net import jm_web_domain
        return f"https://{jm_web_domain(self.settings)}/album/{wid}/"

    def cover_url(self, wid: str) -> str:
        from jmcomic import JmcomicText
        try:
            return JmcomicText.get_album_cover_url(wid, size="_3x4")
        except Exception:
            return ""

    def _from_item(self, aid: str, info: dict) -> Work:
        author = info.get("author") or ""
        authors = [a.strip() for a in re.split(r"[,，、]", author) if a.strip()] if isinstance(author, str) else list(author)
        cat = info.get("category") or {}
        sub = info.get("category_sub") or {}
        tags = list(info.get("tags") or [])
        w = Work(
            site="jm", wid=str(aid), title=str(info.get("name") or ""),
            authors=authors, tags=tags,
            category=(cat.get("title") if isinstance(cat, dict) else str(cat)) or "",
            description=str(info.get("description") or ""),
            cover=self.cover_url(aid), url=self.web_url(aid),
        )
        for t in (w.category, sub.get("title") if isinstance(sub, dict) else ""):
            if t and t not in w.tags:
                w.tags.append(t)
        return enrich_from_title(w)

    def _page_items(self, page) -> list[Work]:
        out = []
        for aid, info in page.content:
            out.append(self._from_item(aid, info if isinstance(info, dict) else {}))
        return out

    # ---------- 收藏 ----------
    def fetch_favorites(self, progress: Progress | None = None, max_pages: int | None = None) -> list[Work]:
        if not self.logged_in():
            raise SiteError("请先登录禁漫")
        cl = self.client()
        works: list[Work] = []
        page_no, total_pages = 1, 1
        while page_no <= total_pages:
            self.throttle.wait()
            try:
                # mr = 按收藏时间排序，新收藏在前
                page = cl.favorite_folder(page=page_no, order_by="mr", folder_id="0")
            except Exception as e:
                raise SiteError(f"读取禁漫收藏第 {page_no} 页失败：{e}") from e
            total_pages = max(1, page.page_count)
            if max_pages:
                total_pages = min(total_pages, max_pages)
            works.extend(self._page_items(page))
            if progress:
                progress(f"禁漫：读取收藏列表 第 {page_no}/{total_pages} 页（已读 {len(works)} 本）", page_no, total_pages)
            page_no += 1
        return works

    # ---------- 详情 ----------
    def fetch_detail(self, wid: str) -> tuple[Work, list[Work]]:
        cl = self.client()
        self.throttle.wait()
        try:
            a = cl.get_album_detail(wid, allow_redirect=False)
        except TypeError:
            a = cl.get_album_detail(wid)
        except Exception as e:
            raise SiteError(f"读取禁漫作品 {wid} 失败：{e}") from e
        w = Work(
            site="jm", wid=str(a.album_id), title=a.name,
            authors=list(a.authors or []), tags=list(a.tags or []),
            parodies=list(a.works or []), description=a.description or "",
            cover=self.cover_url(a.album_id), url=self.web_url(a.album_id),
            popularity=_num(getattr(a, "views", 0)) + 20 * _num(getattr(a, "likes", 0)),
            detail_ok=True,
        )
        enrich_from_title(w)
        related = []
        for it in getattr(a, "related_list", None) or []:
            if isinstance(it, dict) and it.get("id"):
                related.append(self._from_item(str(it["id"]), it))
        return w, related

    # ---------- 检索 ----------
    _MODES = {"site": 0, "work": 1, "author": 2, "tag": 3}
    _ORDERS = {"latest": "mr", "view": "mv", "like": "tf", "score": "tr"}

    def search(self, query: str, mode: str = "site", page: int = 1, order: str = "latest") -> list[Work]:
        cl = self.client()
        self.throttle.wait()
        try:
            res = cl.search(query, page, self._MODES.get(mode, 0), self._ORDERS.get(order, "mr"), "a", "0", None)
        except Exception as e:
            raise SiteError(f"禁漫搜索“{query}”失败：{e}") from e
        if getattr(res, "is_single_album", False):
            try:
                a = res.single_album
                return [self._from_item(a.album_id, {"name": a.name, "author": ",".join(a.authors), "tags": a.tags})]
            except Exception:
                return []
        return self._page_items(res)

    BROWSE_CATEGORIES = ["doujin", "single", "short", "another", "hanManga"]

    def browse(self, page: int = 1, category: str = "all", order: str = "latest") -> list[Work]:
        from jmcomic import JmMagicConstants as M
        cats = {"all": M.CATEGORY_ALL, "doujin": M.CATEGORY_DOUJIN, "single": M.CATEGORY_SINGLE,
                "short": M.CATEGORY_SHORT, "another": M.CATEGORY_ANOTHER, "hanManga": M.CATEGORY_HANMAN}
        orders = {"latest": M.ORDER_BY_LATEST, "view": M.ORDER_BY_VIEW, "like": M.ORDER_BY_LIKE}
        cl = self.client()
        self.throttle.wait()
        try:
            res = cl.categories_filter(page, M.TIME_ALL, cats.get(category, M.CATEGORY_ALL),
                                       orders.get(order, M.ORDER_BY_LATEST))
        except Exception as e:
            raise SiteError(f"浏览禁漫分类失败：{e}") from e
        return self._page_items(res)

    def ranking(self, kind: str = "week", page: int = 1) -> list[Work]:
        cl = self.client()
        self.throttle.wait()
        try:
            res = cl.week_ranking(page) if kind == "week" else cl.month_ranking(page)
        except Exception as e:
            raise SiteError(f"读取禁漫排行失败：{e}") from e
        return self._page_items(res)
