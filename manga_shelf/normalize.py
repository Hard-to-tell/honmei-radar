"""文本规范化与同人本标题拆解。

两站大量作品的标题形如：
    [汉化组] (C99) [社团 (作者)] 标题 (原作) [中国翻译] [无修正]
作者、社团、原作、汉化组都藏在标题里。这里把它们拆出来，作为作者和标签信号，
同时得到去掉方括号的“核心标题”，用于跨站合并和语义匹配。
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .config import data_dir

try:
    from opencc import OpenCC
    _t2s = OpenCC("t2s")
except Exception:  # pragma: no cover
    _t2s = None


@lru_cache(maxsize=50000)
def to_simplified(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return _t2s.convert(s) if _t2s else s


_PUNCT = re.compile(r"[\s\-_~・·.,，。!！?？:：;；'\"“”‘’/\\|]+")


def norm(s: str) -> str:
    """比较用的键：简体、小写、全角转半角、去空白和标点。"""
    return _PUNCT.sub("", to_simplified(s).lower())


# ---------- 标签 ----------
# 语言、版本这类标签不代表口味，打分时忽略
META_TAGS = {
    "禁漫书库", "禁漫汉化组", "中文", "汉化", "中国翻译", "中国翻訳", "中国语", "中国語", "日文", "日语", "日語", "英文", "english",
    "chinese", "japanese", "dl版", "重新上传", "重嵌", "扫图", "掃圖", "单行本", "單行本", "已完结", "完结",
    "连载中", "连载", "pixiv", "fanbox", "fantia", "patreon", "cg集", "cg", "无修正", "無修正",
}

# 同义标签，键会被规范化后匹配。用户可在数据目录的 tag_aliases.json 里追加
DEFAULT_ALIASES = {
    "彩色": "全彩", "full color": "全彩", "fullcolor": "全彩",
    "长篇": "长篇", "長篇": "长篇",
    "ntr": "ntr", "寝取": "ntr", "寝取り": "ntr", "寝取られ": "ntr", "绿帽": "ntr",
    "纯爱": "纯爱", "純愛": "纯爱",
    "后宫": "后宫", "後宮": "后宫", "harem": "后宫",
    "百合": "百合", "yuri": "百合", "gl": "百合",
    "耽美": "耽美", "bl": "耽美", "yaoi": "耽美",
    "巨乳": "巨乳", "大乳": "巨乳", "爆乳": "巨乳",
    "贫乳": "贫乳", "貧乳": "贫乳", "小胸": "贫乳",
    "萝莉": "萝莉", "蘿莉": "萝莉", "loli": "萝莉",
    "人妻": "人妻", "熟女": "熟女",
    "触手": "触手", "觸手": "触手",
    "伪娘": "伪娘", "偽娘": "伪娘", "男の娘": "伪娘", "扶他": "扶他", "扶她": "扶他", "futanari": "扶他",
    "韩漫": "韩漫", "韓漫": "韩漫", "同人": "同人", "单本": "单本", "短篇": "短篇",
    "3d": "3d", "cosplay": "cosplay",
    # 两站写法不同的同一标签
    "透视": "横切面", "断面图": "横切面", "x光": "横切面",
    "剧情向": "剧情", "阿黑颜": "阿嘿颜", "骑乗": "骑乘", "骑乘位": "骑乘",
    "过膝袜": "长筒袜", "长筒丝袜": "长筒袜", "连裤丝袜": "连裤袜", "黑丝": "丝袜",
    "女高中生(jk)": "jk", "女高中生": "jk", "妹妹系": "妹妹", "姐姐系": "姐姐",
    "后宫闪光": "后宫", "手淫": "自慰", "群交": "3p", "多人": "3p", "肛交": "肛门",
    "乱伦": "近亲", "近亲相交": "近亲", "出轨": "ntr",
    "近亲相交:妹": "近亲", "近亲相交:姊": "近亲", "近亲相交:母": "近亲", "近亲相交:父": "近亲",
    "近亲相奸": "近亲", "近亲乱伦": "近亲", "兄妹乱伦": "近亲",
    "破处": "处女", "情趣内衣": "内衣", "野外 / 露出": "露出", "野外露出": "露出",
    "洗脑 / 催眠": "催眠", "骑大车": "骑乘", "兽耳娘": "兽耳", "足の恋": "足交",
    # 哔咔的分类名
    "扶他乐园": "扶他", "伪娘哲学": "伪娘", "百合花园": "百合", "耽美花园": "耽美", "重口地带": "重口",
}

# 同一原作的不同译名，合并到一个名字
PARODY_ALIASES = {
    "アイドルマスターシャイニーカラーズ": "偶像大师 闪耀色彩", "シャイニーカラーズ": "偶像大师 闪耀色彩",
    "闪耀色彩": "偶像大师 闪耀色彩", "偶像大师闪耀色彩": "偶像大师 闪耀色彩", "偶像大师": "偶像大师",
    "アイドルマスター": "偶像大师", "アイドルマスターシンデレラガールズ": "偶像大师 灰姑娘女孩",
    "ブルーアーカイブ": "蔚蓝档案", "蔚蓝档案": "蔚蓝档案", "bluearchive": "蔚蓝档案",
    "原神": "原神", "genshinimpact": "原神",
    "崩坏星穹铁道": "崩坏：星穹铁道", "崩壊スターレイル": "崩坏：星穹铁道", "崩坏：星穹铁道": "崩坏：星穹铁道",
    "ウマ娘プリティーダービー": "赛马娘", "赛马娘": "赛马娘", "东方project": "东方Project", "東方project": "东方Project",
    "五等分の花嫁": "五等分的新娘", "五等分的新娘": "五等分的新娘",
}


def norm_parody(p: str) -> str:
    k = norm(p)
    return PARODY_ALIASES.get(k, PARODY_ALIASES.get(k.replace(" ", ""), to_simplified(p).strip()))



USE_LEARNED = True   # 评测旧版本时临时关掉学到的对照

# ---------- 标题里的设定词 ----------
# 标签常常漏标，但标题会直说“幼驯染”“女仆”“学姐”。只看拆出来的正标题（不看社团名、原作、汉化组）。
TITLE_HINTS: list[tuple[str, str]] = [
    (r"メイド|女仆|女僕|maid", "女仆"),
    (r"催眠|さいみん", "催眠"),
    (r"お姉|姉さん|姉ちゃん|姐姐|姊姊|大姐|おねえ", "姐姐"),
    (r"(?<![辣姐姉姊兄黑太])妹(?![控])|いもうと", "妹妹"),
    (r"人妻|若妻|新妻|愛妻|爱妻|の妻|妻が|妻を|妻は|妻の", "人妻"),
    (r"寝取|寝とら|ネトラレ|ねとられ|ntr|出轨|出軌|绿帽", "ntr"),
    (r"幼馴染|幼なじみ|おさななじみ|青梅竹马|青梅竹馬", "青梅竹马"),
    (r"先輩|せんぱい|センパイ|前辈|前輩|学姐|學姐", "前辈"),
    (r"後輩|こうはい|コウハイ|后辈|後輩|学妹|學妹", "后辈"),
    (r"先生(?!方)|老师|老師|教師|教师", "老师"),
    (r"サキュバス|魅魔|淫魔", "魅魔"),
    (r"ギャル|辣妹", "辣妹"),
    (r"異世界|异世界", "异世界"),
    (r"義母|继母|繼母|お母さん|母さん|ママ(?!活)|妈妈|媽媽", "母亲"),
    (r"彼女(?!たち)|女友|女朋友|カノジョ", "女友"),
    (r"jk|女子校生|女子高生|女高中生", "jk"),
    (r"エルフ|精灵|精靈", "精灵"),
    (r"ナース|护士|護士|看護", "护士"),
    (r"上司|部下|ol(?![a-z])|女社员|女社員", "职场"),
]
_TITLE_HINTS = [(re.compile(p, re.I), t) for p, t in TITLE_HINTS]
USE_TITLE_HINTS = True


@lru_cache(maxsize=50000)
def title_tags(title: str) -> tuple[str, ...]:
    if not USE_TITLE_HINTS or not title:
        return ()
    core = parse_title(title).core or ""
    s = to_simplified(core).lower()
    return tuple(dict.fromkeys(t for rx, t in _TITLE_HINTS if rx.search(core) or rx.search(s)))


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    table = {norm(k): v for k, v in DEFAULT_ALIASES.items()}
    # learned_aliases.json：从两站都收藏的同一本里自动学到的对照；tag_aliases.json：你手动加的（优先）
    for name in (("learned_aliases.json",) if USE_LEARNED else ()) + ("tag_aliases.json",):
        p = data_dir() / name
        if p.exists():
            try:
                for k, v in json.loads(p.read_text("utf-8")).items():
                    table[norm(k)] = to_simplified(v)
            except Exception:
                pass
    return table


def reset_aliases() -> None:
    _aliases.cache_clear()
    norm_tag.cache_clear()


@lru_cache(maxsize=1)
def _meta_keys() -> frozenset[str]:
    return frozenset(norm(m) for m in META_TAGS)


@lru_cache(maxsize=100000)
def norm_tag(t: str) -> str | None:
    k = norm(t)
    if not k:
        return None
    k = _aliases().get(k, to_simplified(t).strip().lower())
    if norm(k) in _meta_keys():
        return None
    return k


def norm_tags(tags) -> list[str]:
    out, seen = [], set()
    for t in tags or []:
        n = norm_tag(t)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def norm_person(name: str) -> str:
    return norm(name)


# ---------- 标题拆解 ----------
_EVENT = re.compile(
    r"^(c\d{2,3}|comic\s*1|comic1.*|紅楼夢.*|红楼梦.*|例大祭.*|コミティア.*|comitia.*|サンクリ.*|"
    r"cosmic.*|コミック.*|ふたけっと.*|gw超同人祭|.*オンリー.*|.*only.*|\d{4}年.*)$",
    re.I,
)
_GROUP_HINT = re.compile(r"(汉化|漢化|翻译|翻譯|翻訳|个人|個人|嵌字|中文|重嵌|掃圖|扫图|机翻|機翻|工作室|练习|練習)")
_LANG_OR_FLAG = re.compile(
    r"^(中国翻訳|中国翻译|中國翻譯|中国語|中國語|中国语|中文|chinese|english|日文|日语|日語|英訳|"
    r"dl版|無修正|无修正|全彩|カラー|彩色|digital|decensored|uncensored|重嵌|整合|雑誌|雜誌|雑誌寄せ集め|"
    r"中国翻訳\s*.*|.*翻訳.*|.*翻譯.*|.*翻译.*)$",
    re.I,
)
_FLAG_TAGS = {"無修正": "无修正", "无修正": "无修正", "全彩": "全彩", "カラー": "全彩", "彩色": "全彩", "uncensored": "无修正",
              "decensored": "无修正"}
_NOT_AUTHOR = {"pixiv", "fanbox", "fantia", "patreon", "skeb", "twitter", "ai generated", "ai", "中文", "全彩",
               "anthology", "アンソロジー", "雑誌", "杂志"}
_BRACKETS = {"[": "]", "(": ")", "【": "】", "（": "）", "「": "」"}


@dataclass
class ParsedTitle:
    core: str
    artists: list[str] = field(default_factory=list)
    circles: list[str] = field(default_factory=list)
    parodies: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)   # 全彩、无修正等可当标签的标记


def _take_leading(s: str) -> tuple[str, str, str] | None:
    s = s.lstrip()
    if not s or s[0] not in _BRACKETS:
        return None
    close = _BRACKETS[s[0]]
    depth = 0
    for i, ch in enumerate(s):
        if ch == s[0]:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return s[0], s[1:i].strip(), s[i + 1:]
    return None


def _take_trailing(s: str) -> tuple[str, str, str] | None:
    s = s.rstrip()
    if not s:
        return None
    rev = {v: k for k, v in _BRACKETS.items()}
    if s[-1] not in rev:
        return None
    opener = rev[s[-1]]
    depth = 0
    for i in range(len(s) - 1, -1, -1):
        ch = s[i]
        if ch == s[-1]:
            depth += 1
        elif ch == opener:
            depth -= 1
            if depth == 0:
                return opener, s[i + 1:-1].strip(), s[:i]
    return None


def _split_names(s: str) -> list[str]:
    return [p.strip() for p in re.split(r"[、,，&＆×/]| x ", s) if p.strip()]


def _circle_artist(inner: str, pt: ParsedTitle) -> None:
    m = re.match(r"^(.*?)\s*[（(](.+)[）)]\s*$", inner)
    if m and m.group(1).strip():
        pt.circles.extend(_split_names(m.group(1)))
        pt.artists.extend(_split_names(m.group(2)))
    else:
        pt.artists.extend(_split_names(inner))


@lru_cache(maxsize=50000)
def parse_title(title: str) -> ParsedTitle:
    s = unicodedata.normalize("NFKC", title or "").strip()
    pt = ParsedTitle(core="")
    leading: list[tuple[str, str]] = []
    while True:
        r = _take_leading(s)
        if not r:
            break
        b, inner, s = r
        leading.append((b, inner))
    trailing: list[tuple[str, str]] = []
    while True:
        r = _take_trailing(s)
        if not r:
            break
        b, inner, rest = r
        if not rest.strip():  # 整个剩余都是括号，保留作为标题
            break
        trailing.append((b, inner))
        s = rest
    pt.core = s.strip() or (leading[-1][1] if leading else title)

    # 前缀：汉化组 / 展会 / [社团 (作者)]
    author_slots = []
    for b, inner in leading:
        if not inner:
            continue
        if b in "(（" and _EVENT.match(inner):
            continue
        if b in "[【" and _GROUP_HINT.search(inner):
            pt.groups.append(inner)
            continue
        if b in "[【":
            author_slots.append(inner)
    author_slots = [a for a in author_slots if a.lower() not in _NOT_AUTHOR]
    if author_slots:
        _circle_artist(author_slots[-1], pt)
        for extra in author_slots[:-1]:
            if not _LANG_OR_FLAG.match(extra):
                pt.groups.append(extra)

    # 后缀：(原作) [语言/标记/汉化组]
    for b, inner in trailing:
        if not inner:
            continue
        low = inner.lower()
        flag = _FLAG_TAGS.get(inner) or _FLAG_TAGS.get(low)
        if flag:
            pt.flags.append(flag)
            continue
        if b in "(（":
            if re.match(r"^(第?\d+([-~]\d+)?[话話卷巻]?|vol\.?\s*\d+|上|中|下|前篇|后篇|後編|前編)$", low):
                continue
            if not _LANG_OR_FLAG.match(inner) and not _EVENT.match(inner):
                pt.parodies.extend(_split_names(inner))
        else:
            if _LANG_OR_FLAG.match(inner):
                continue
            if _GROUP_HINT.search(inner):
                pt.groups.append(inner)
            elif re.match(r"^(第?\d+[话話卷巻]?|vol\.?\s*\d+|\d+-\d+)$", low):
                continue
            else:
                pt.groups.append(inner)
    pt.parodies = [p for p in pt.parodies if p.lower() not in {"オリジナル", "original", "原创", "原創"}]
    return pt


def title_key(title: str) -> str:
    """跨站匹配用的核心标题键。"""
    return norm(parse_title(title).core)


_SEG = re.compile(r"[\[【(（「][^\]】)）」]*[\]】)）」]")


def dedup_keys(title: str, authors: list[str] | None = None) -> set[str]:
    """判断“同一本的不同版本”（不同汉化组、重嵌、无修、DL 版、中日双标题）用的一组键，有一个相同就算同一本。
    - 核心标题（够长时单独成键；短标题要和作者一起才算）
    - 去掉所有括号后剩下的每一段文字（常见“中文标题 [组] [作者] 日文标题”）"""
    pt = parse_title(title)
    keys: set[str] = set()
    core = norm(pt.core)
    people = {norm(a) for a in (authors or []) + pt.artists + pt.circles if norm(a)}
    if len(core) >= 6:
        keys.add("t:" + core)
    if core:
        for a in people:
            keys.add("a:" + a + "|" + core)
    for seg in _SEG.sub("|", unicodedata.normalize("NFKC", title or "")).split("|"):
        k = norm(seg)
        if len(k) >= 6:
            keys.add("t:" + k)
    return keys
