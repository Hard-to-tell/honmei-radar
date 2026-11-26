"""哔咔适配器：使用哔咔官方 App / 网页版共用的接口（picaapi.picacomic.com）。

登录用邮箱/用户名和密码换取 token，token 只保存在本机数据库里。
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import requests

from ..config import load_settings, proxies
from ..db import DB
from ..models import Work
from .base import Progress, SiteAdapter, SiteError, Throttle, enrich_from_title

API = "https://picaapi.picacomic.com/"
API_KEY = "C69BAF41DA5ABD1FFEDC6D2FEA56B"
SIGN_KEY = "~d}$Q7$eIni=V)9\\RK/P.RM4;9[7|@/CA}b~OW!3?EV`:<>M7pddUBL5n|0/*Cn"


def sign_headers(path_with_query: str, method: str, now: int | None = None, nonce: str | None = None) -> dict:
    """生成哔咔接口要求的签名头。path 不带开头的 '/'，含查询串。"""
    ts = str(int(now if now is not None else time.time()))
    nonce = nonce or uuid.uuid4().hex
    raw = (path_with_query + ts + nonce + method.upper() + API_KEY).lower()
    sig = hmac.new(SIGN_KEY.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return {
        "time": ts,
        "nonce": nonce,
        "signature": sig,
        "accept": "application/vnd.picacomic.com.v1+json",
        "api-key": API_KEY,
        "app-channel": "2",
        "app-version": "2.2.1.2.3.3",
        "app-uuid": "defaultUuid",
        "app-platform": "android",
        "app-build-version": "44",
        "image-quality": "original",
        "user-agent": "okhttp/3.8.1",
        "content-type": "application/json; charset=UTF-8",
    }


def thumb_url(thumb: dict | None) -> str:
    if not thumb or not thumb.get("path"):
        return ""
    server = (thumb.get("fileServer") or "https://storage1.picacomic.com").rstrip("/")
    path = thumb["path"].lstrip("/")
    if "/static/" in server or path.startswith("static/"):
        return f"{server}/{path}"
    return f"{server}/static/{path}"


class PicaAdapter(SiteAdapter):
    site = "pica"
    name = "哔咔"

    def __init__(self, db: DB):
        self.db = db
        self.settings = load_settings()
        self.throttle = Throttle(float(self.settings.get("rate_interval", 0.15)))
        self.session = requests.Session()
        from .. import net
        net.apply(self.settings)
        px = proxies(self.settings)
        if px:
            self.session.trust_env = False
            self.session.proxies.update(px)

    # ---------- 底层请求 ----------
    def _req(self, method: str, path: str, params: dict | None = None, json_body: dict | None = None,
             auth: bool = True) -> dict:
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        full = path.lstrip("/") + query
        headers = sign_headers(full, method)
        if auth:
            token = self.db.secret_get("pica", "token")
            if not token:
                raise SiteError("请先登录哔咔")
            headers["authorization"] = token
        self.throttle.wait()
        try:
            r = self.session.request(method, API + full, headers=headers, json=json_body, timeout=15)
        except requests.RequestException as e:
            raise SiteError("连不上哔咔。书架已经自动试过备用线路；还不行的话，打开你平时用的加速器 / 代理，"
                            "再到 设置 → 网络 点“重新检测”。") from e
        try:
            data = r.json()
        except ValueError:
            raise SiteError(f"哔咔接口返回异常（HTTP {r.status_code}）")
        if r.status_code == 401 and auth:
            # token 过期：用保存的账号自动重登一次
            u, p = self.db.secret_get("pica", "username"), self.db.secret_get("pica", "password")
            if u and p and not getattr(self, "_relogging", False):
                self._relogging = True
                try:
                    self.login(u, p)
                finally:
                    self._relogging = False
                return self._req(method, path, params, json_body, auth)
            raise SiteError("哔咔登录已过期，请重新登录")
        if r.status_code >= 400 or data.get("code", 200) >= 400:
            raise SiteError(f"哔咔接口错误：{data.get('message') or data.get('error') or r.status_code}")
        return data.get("data") or {}

    # ---------- 登录 ----------
    def login(self, username: str, password: str) -> str:
        data = self._req("POST", "auth/sign-in", json_body={"email": username, "password": password}, auth=False)
        token = data.get("token")
        if not token:
            raise SiteError("哔咔登录失败：没有拿到 token")
        self.db.secret_set("pica", "token", token)
        self.db.secret_set("pica", "username", username)
        self.db.secret_set("pica", "password", password)
        return "ok"

    def logged_in(self) -> bool:
        return bool(self.db.secret_get("pica", "token"))

    # ---------- 转换 ----------
    def web_url(self, wid: str) -> str:
        return self.settings["pica"]["web_url_template"].format(id=wid)

    def _from_doc(self, d: dict, detail: bool = False) -> Work:
        author = d.get("author") or ""
        w = Work(
            site="pica", wid=str(d.get("_id") or d.get("id")), title=str(d.get("title") or ""),
            authors=[author] if author else [],
            tags=list(d.get("categories") or []) + list(d.get("tags") or []),
            description=str(d.get("description") or ""),
            cover=thumb_url(d.get("thumb")),
            popularity=float(d.get("totalViews") or d.get("viewsCount") or 0)
            + 20 * float(d.get("totalLikes") or d.get("likesCount") or 0),
            detail_ok=detail,
        )
        if d.get("chineseTeam"):
            w.groups.append(str(d["chineseTeam"]))
        w.url = self.web_url(w.wid)
        # 哔咔的 author 字段常是“社团 (作者)”，用标题拆解统一处理
        from ..normalize import parse_title
        if author:
            pt = parse_title(f"[{author}] x")
            if pt.artists:
                w.authors = pt.artists
                w.circles = pt.circles
        return enrich_from_title(w)

    # ---------- 收藏 ----------
    def fetch_favorites(self, progress: Progress | None = None, max_pages: int | None = None) -> list[Work]:
        works: list[Work] = []
        page_no, pages = 1, 1
        while page_no <= pages:
            # dd = 新到旧（按收藏时间）
            data = self._req("GET", "users/favourite", params={"s": "dd", "page": page_no})
            comics = data.get("comics") or {}
            pages = int(comics.get("pages") or 1)
            if max_pages:
                pages = min(pages, max_pages)
            for d in comics.get("docs") or []:
                works.append(self._from_doc(d))
            if progress:
                progress(f"哔咔：读取收藏列表 第 {page_no}/{pages} 页（已读 {len(works)} 本）", page_no, pages)
            page_no += 1
        return works

    def fetch_detail(self, wid: str) -> tuple[Work, list[Work]]:
        data = self._req("GET", f"comics/{wid}")
        comic = data.get("comic") or {}
        if not comic:
            raise SiteError(f"哔咔作品 {wid} 不存在或已下架")
        return self._from_doc(comic, detail=True), []
