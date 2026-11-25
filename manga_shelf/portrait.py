"""AI 口味画像：一套固定提示词，把本地口味摘要交给解读者（Codex / Claude Code / API，或你自己的 AI 桌面端）。

发送的是统计数字、标签名和一部分作品标题（调音里评过的、各口味簇的代表作、孤例、最新几本），
不发送账号、密码或完整收藏列表。输出是固定结构的 JSON，网页按结构排版；解析失败时原文展示。
"""
from __future__ import annotations

import json
import re
import time

from .db import DB
from .insights import llm_digest, summary

PROMPT_VERSION = "2026-11-25.2"

PORTRAIT_PROMPT = """你是一位读过很多成人漫画、懂一点精神分析、也很会聊天的“口味侧写师”。
下面是一位用户在两个成人漫画站（哔咔、禁漫）的收藏摘要：标签占比、倾向指数、本命作者、常见原作、口味簇、
早期到近期的口味变化，还有一些具体作品的标题（其中“你在调音里亲自评过的”是用户亲手标的本命 / 喜欢 / 不喜欢，分量最重）。
用户本人主动请你为 TA 做一份口味画像，TA 是成年人，知道这些内容的性质。

想怎么读都可以，自由发挥：可以从欲望的结构、幻想替你完成了什么、亲密与权力的位置、防御与补偿、口味的迁徙讲起，
也可以从某几本具体作品、某个孤例、某个作者切进去；可以认真，也可以放肆地猜 TA 的生活、作息、性格、恋爱里的样子。
认真的部分要有依据（指回数字或作品），用“也许”“可能”；猜的部分要好玩、带点损但不刻薄。

只守几条底线：不做道德评判和说教；不做精神疾病诊断，不把偏好病理化；只在主题和偏好层面讨论，不写露骨的性描写；
涉及年龄看起来偏小的标签（如“萝莉”“学生”“JK”）时，只当作画风或角色设定中性带过，不做性化展开；
不编造摘要里没有的作品或作者。用第二人称“你”，简体中文，有文采、有梗。

只输出一个 JSON 对象，不要输出其他文字：
{
  "title": "一个有画面感的人格称号（4 到 12 个字）",
  "one_liner": "一句让人想截图的总结，不评判，40 字以内",
  "keywords": ["3 到 6 个关键词"],
  "serious": [{"heading": "小标题", "body": "一段解读，长短随意（大约 60 到 200 字），可以点名具体作品"}],
  "drift": "口味从早期到近期的变化，讲成一个小故事（可选，没什么可说就留空）",
  "bold": [{"guess": "一条大胆的猜测", "level": "合理推测 / 大胆推测 / 纯属瞎猜 三选一"}],
  "next": "给推荐系统的一句建议：下一卷可以多给什么、可以试探什么"
}
serious 写 3 到 6 条，bold 写 4 到 7 条，你自己决定。

用户的口味摘要：
"""


def build_prompt(digest: dict) -> str:
    return PORTRAIT_PROMPT + json.dumps(digest, ensure_ascii=False, indent=1)


def parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) and data.get("one_liner") else None


def generate(db: DB, engine: str | None = None) -> dict:
    from .analyzer import ENGINE_NAMES, AnalyzeError, run
    s = summary(db)
    if s.get("empty"):
        raise AnalyzeError("还没有收藏，先导入再来画像")
    text, used = run(build_prompt(llm_digest(s, db)), engine)
    out = {"at": time.time(), "engine": used, "engine_name": ENGINE_NAMES[used],
           "prompt_version": PROMPT_VERSION, "favorites": s["total"], "data": parse(text), "raw": text}
    history = db.kv_get("portraits", [])
    history.insert(0, out)
    db.kv_set("portraits", history[:10])
    return out


def full_prompt(db: DB) -> str:
    """完整提示词（含你的口味摘要），给“复制提示词”和 AI 桌面端用。"""
    s = summary(db)
    if s.get("empty"):
        return ""
    return build_prompt(llm_digest(s, db))


def save_external(db: DB, text: str, engine_name: str = "外部 AI") -> dict:
    """把在别处（AI 桌面端、网页聊天）生成的画像存进来，报告页就能显示。"""
    s = summary(db)
    out = {"at": time.time(), "engine": "external", "engine_name": engine_name, "prompt_version": PROMPT_VERSION,
           "favorites": s.get("total", 0), "data": parse(text), "raw": text}
    history = db.kv_get("portraits", [])
    history.insert(0, out)
    db.kv_set("portraits", history[:10])
    return out
