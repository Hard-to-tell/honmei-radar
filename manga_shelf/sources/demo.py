"""演示用的假数据站点：不联网也能把整个流程跑一遍，测试也用它。"""
from __future__ import annotations

import random

from ..models import Work
from .base import Progress, SiteAdapter, enrich_from_title

TAGS = ["纯爱", "NTR", "后宫", "百合", "巨乳", "贫乳", "人妻", "触手", "全彩", "长篇", "校园", "奇幻",
        "伪娘", "制服", "调教", "恋爱", "姐姐", "妹妹", "老师", "异世界", "运动", "眼镜", "泳装", "温泉"]
PARODIES = ["原神", "蔚蓝档案", "公主连结", "东方Project", "赛马娘", "明日方舟", "原创"]
ARTISTS = [f"作者{c}" for c in "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"]
CIRCLES = [f"社团{c}" for c in "ABCDEFGHIJ"]
GROUPS = ["绅士汉化组", "无邪气汉化组", "个人汉化"]


def _catalog(seed: int = 7, n: int = 400) -> list[Work]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        artist = rnd.choice(ARTISTS)
        circle = CIRCLES[ARTISTS.index(artist) % len(CIRCLES)]
        parody = rnd.choice(PARODIES)
        # 每个作者有自己偏好的题材，让数据有结构
        base = ARTISTS.index(artist)
        tags = {TAGS[(base * 3 + k) % len(TAGS)] for k in range(3)}
        tags |= set(rnd.sample(TAGS, 2))
        title = f"[{rnd.choice(GROUPS)}] (C10{rnd.randint(0, 5)}) [{circle} ({artist})] 第{i}号故事" + \
                (f" ({parody})" if parody != "原创" else "") + " [中国翻译]"
        w = Work(site="jm", wid=str(100000 + i), title=title, authors=[artist], tags=sorted(tags),
                 parodies=[] if parody == "原创" else [parody], category="同人",
                 description=f"{'、'.join(sorted(tags))}题材的故事。", popularity=rnd.randint(100, 100000))
        out.append(enrich_from_title(w))
    return out


class DemoAdapter(SiteAdapter):
    """site 参数决定它扮演哪个站：jm 时提供候选与收藏，pica 时只提供收藏。"""

    def __init__(self, site: str = "jm", seed: int = 7):
        self.site = site
        self.name = "演示-" + site
        self.catalog = _catalog(seed)
        self.by_id = {w.wid: w for w in self.catalog}

    def _copy(self, w: Work, detail: bool) -> Work:
        c = Work(**{**w.to_dict()})
        c.site = self.site
        if self.site == "pica":
            c.wid = "p" + w.wid
        if not detail:
            c.tags = [t for t in c.tags if t == "全彩"]
            c.description = ""
        c.detail_ok = detail
        c.url = self.web_url(c.wid)
        return c

    def login(self, username: str, password: str) -> str:
        return "ok"

    def logged_in(self) -> bool:
        return True

    def fetch_favorites(self, progress: Progress | None = None, max_pages: int | None = None) -> list[Work]:
        rnd = random.Random(11 if self.site == "jm" else 13)
        liked_artists = ARTISTS[:4] if self.site == "jm" else ARTISTS[2:6]
        pool = [w for w in self.catalog if w.authors[0] in liked_artists]
        favs = rnd.sample(pool, min(40, len(pool)))
        if progress:
            progress(f"演示：读取收藏列表 {len(favs)} 本", 1, 1)
        return [self._copy(w, detail=False) for w in favs]

    def fetch_detail(self, wid: str) -> tuple[Work, list[Work]]:
        raw = self.by_id[wid.lstrip("p")]
        rel = [w for w in self.catalog if w.authors == raw.authors and w.wid != raw.wid][:5]
        return self._copy(raw, detail=True), [self._copy(w, detail=False) for w in rel]

    def search(self, query: str, mode: str = "site", page: int = 1, order: str = "latest") -> list[Work]:
        words = query.split()
        hits = []
        for w in self.catalog:
            hay = " ".join([w.title] + w.authors + w.tags + w.parodies + w.circles)
            if all(x in hay for x in words):
                hits.append(w)
        if order in ("view", "like"):
            hits.sort(key=lambda w: -w.popularity)
        return [self._copy(w, detail=False) for w in hits[(page - 1) * 20: page * 20]]

    def ranking(self, kind: str = "week", page: int = 1) -> list[Work]:
        if kind == "week":
            hits = sorted(self.catalog, key=lambda w: -w.popularity)
        else:  # 月榜换一种次序，让两份榜单不完全重合
            hits = sorted(self.catalog, key=lambda w: (int(w.wid) * 37) % 101)
        return [self._copy(w, detail=False) for w in hits[(page - 1) * 20: page * 20]]

    BROWSE_CATEGORIES = ["a", "b", "c"]

    def browse(self, page: int = 1, category: str = "all", order: str = "latest") -> list[Work]:
        salt = {"all": 1, "a": 3, "b": 7, "c": 11}.get(category, 1)
        hits = sorted(self.catalog, key=lambda w: (int(w.wid) * 53 * salt) % 389)
        return [self._copy(w, detail=False) for w in hits[(page - 1) * 20: page * 20]]

    def web_url(self, wid: str) -> str:
        return f"https://example.com/{self.site}/{wid}"
