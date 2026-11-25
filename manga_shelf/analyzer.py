"""解读：把本地推荐证据交给大模型做比较和总结。

可选三种引擎，都不是必需的：
  - api     OpenAI 兼容接口（在设置里填地址、Key、模型名）
  - codex   本机已安装的 Codex CLI（codex exec），用你自己的 Codex 登录
  - claude  本机已安装的 Claude Code CLI（claude -p）
只发送作品标题、作者、标签、匹配证据和最接近的几本收藏；账号凭据永远不会发送。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter

import requests

from .config import load_settings, proxies
from .db import DB
from .explain import evidence
from .normalize import parse_title

ENGINE_NAMES = {"api": "API", "codex": "Codex", "claude": "Claude Code"}
TIMEOUT = 240
# 默认用便宜一点的模型；某个模型你的账号用不了就自动换下一个，最后才用你本机的默认模型
CODEX_MODELS = ["gpt-6.1-sol", "gpt-6.2-sol", "gpt-6-luna", "gpt-6.1-terra", ""]
CLAUDE_MODELS = ["sonnet", ""]
_MODEL_ERR = ("model_not_found", "not supported", "not_in_accessible_catalog", "does not exist",
              "invalid model", "unknown model", "model not found", "not available")


class AnalyzeError(Exception):
    pass


class NoModelError(AnalyzeError):
    """这个引擎现在用不了（模型都不支持，或者登录过期），可以换别的引擎再试。"""


_AUTH_ERR = ("401", "unauthorized", "expired", "authenticate", "not logged in", "log in", "login", "re-auth")
LOGIN_HINT = {"codex": "在命令行运行一次 codex login 重新登录", "claude": "在命令行运行一次 claude，按提示重新登录",
              "api": "检查设置里的 API Key"}


def _which(name: str) -> str | None:
    return shutil.which(name) or shutil.which(name + ".cmd") or shutil.which(name + ".exe")


def available(settings: dict | None = None) -> dict[str, bool]:
    s = settings or load_settings()
    llm = s.get("llm") or {}
    return {
        "api": bool(llm.get("base_url") and llm.get("api_key") and llm.get("model")),
        "codex": _which("codex") is not None,
        "claude": _which("claude") is not None,
    }


def pick_engine(settings: dict | None = None) -> str | None:
    s = settings or load_settings()
    avail = available(s)
    pref = (s.get("analyze") or {}).get("engine") or "auto"
    if pref != "auto" and avail.get(pref):
        return pref
    for e in ("codex", "claude", "api"):
        if avail[e]:
            return e
    return None


# ---------- 各引擎 ----------
def _run_api(prompt: str, s: dict) -> str:
    cfg = s.get("llm") or {}
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    r = requests.post(url, headers={"Authorization": f"Bearer {cfg['api_key']}"},
                      json={"model": cfg["model"], "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.5},
                      timeout=TIMEOUT, proxies=proxies(s))
    if not r.ok:
        raise AnalyzeError(f"接口返回 {r.status_code}：{r.text[:200]}")
    return r.json()["choices"][0]["message"]["content"].strip()


def _run_cli(args: list[str], prompt: str, out_file: str | None = None) -> str:
    try:
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=TIMEOUT, cwd=tempfile.gettempdir(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        raise AnalyzeError("等了 4 分钟还没回来，先放弃了")
    except OSError as e:
        raise AnalyzeError(f"启动失败：{e}")
    if out_file and os.path.exists(out_file):
        txt = open(out_file, encoding="utf-8", errors="replace").read().strip()
        if txt:
            return txt
    if r.returncode != 0:
        raise AnalyzeError((r.stderr or r.stdout or "").strip()[-400:] or f"退出码 {r.returncode}")
    return (r.stdout or "").strip()


def _clean_error(msg: str) -> str:
    """CLI 报错常常是一大段 JSON，挑出人能看懂的那句。"""
    import re
    m = re.findall(r'"message"\s*:\s*"([^"]+)"', msg)
    return m[-1] if m else msg.strip()[-300:]


def _with_models(kind: str, models: list[str], call) -> str:
    """按顺序试模型；模型不可用才换下一个，其他错误直接报。能用的模型记下来，下次直接用。"""
    from .config import save_settings
    s = load_settings()
    a = s.get("analyze") or {}
    custom = (a.get(f"{kind}_model") or "").strip()
    order = [custom] if custom else []
    ok = a.get(f"{kind}_model_ok")
    if ok is not None and not custom:
        order.append(ok)
    order += [m for m in models if m not in order]
    import logging
    log = logging.getLogger("honmei")
    tried: list[str] = []
    for m in order:
        try:
            out = call(m)
        except AnalyzeError as e:
            last = str(e)
            log.warning("%s 模型 %s 失败：%s", kind, m or "（默认）", _clean_error(last))
            if any(k in last.lower() for k in _AUTH_ERR) and not any(k in last.lower() for k in _MODEL_ERR):
                raise NoModelError(f"{ENGINE_NAMES.get(kind, kind)}：登录过期了（{LOGIN_HINT.get(kind, '重新登录')}）")
            if any(k in last.lower() for k in _MODEL_ERR):
                tried.append(f"{m or '默认'}：{_clean_error(last)[:120]}")
                continue
            raise AnalyzeError(_clean_error(last))
        if not custom and ok != m:
            save_settings({"analyze": {**a, f"{kind}_model_ok": m}})
        return out
    raise NoModelError(f"{ENGINE_NAMES.get(kind, kind)}：试过的 {len(tried)} 个模型你的账号都用不了（详情在 data/app.log；"
                       f"可以在 设置 → 解读 → 模型 里填一个能用的）")


def _run_codex(prompt: str) -> str:
    exe = _which("codex")

    def call(model: str) -> str:
        fd, out = tempfile.mkstemp(suffix=".txt", prefix="honmei-")
        os.close(fd)
        try:
            args = [exe, "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--output-last-message", out]
            if model:
                args += ["-m", model]
            return _run_cli(args + ["-"], prompt, out)
        finally:
            try:
                os.remove(out)
            except OSError:
                pass
    return _with_models("codex", CODEX_MODELS, call)


def _run_claude(prompt: str) -> str:
    exe = _which("claude")

    def call(model: str) -> str:
        args = [exe, "-p", "--output-format", "text"]
        if model:
            args += ["--model", model]
        return _run_cli(args, prompt)
    return _with_models("claude", CLAUDE_MODELS, call)


def _run_one(engine: str, prompt: str, s: dict) -> str:
    if engine == "api":
        return _run_api(prompt, s)
    if engine == "codex":
        return _run_codex(prompt)
    return _run_claude(prompt)


def run(prompt: str, engine: str | None = None) -> tuple[str, str]:
    s = load_settings()
    first = engine or pick_engine(s)
    if not first:
        raise AnalyzeError("没有可用的解读引擎：在设置里填一个 API，或在本机安装 Codex / Claude Code")
    avail = available(s)
    if not avail.get(first):
        raise AnalyzeError(f"{ENGINE_NAMES.get(first, first)} 不可用")
    # 一个引擎的模型全都用不了时，自动换本机另一个可用的引擎
    order = [first] + [e for e in ("codex", "claude", "api") if e != first and avail.get(e)]
    errors = []
    for e in order:
        try:
            return _run_one(e, prompt, s), e
        except NoModelError as err:
            errors.append(str(err))
    raise AnalyzeError("\n".join(errors))


# ---------- 提示词 ----------
STYLE = ("用简体中文回答，语气像一位懂行、说话有点文气但不矫情的书店店员。"
         "只依据给出的文字证据（标题、作者、标签、匹配依据），不要编造剧情，也不要断言画风。不要输出 Markdown 表格。")


def batch_evidence(db: DB, batch_id: int) -> dict:
    from .recommend import batch_view, build_profile, taste_summary
    b = batch_view(db, batch_id)
    if not b:
        raise AnalyzeError("找不到这一卷")
    prof = build_profile(db)
    taste = taste_summary(prof, top=10)
    fb = Counter(it["feedback"] for it in b["items"] if it["feedback"])
    return {
        "volume": b.get("vol", batch_id),
        "your_taste": {**taste, "favorites_count": len(prof.favorites)},
        "feedback_in_this_volume": {"心动": fb.get("want", 0), "略过": fb.get("skip", 0), "不喜欢": fb.get("dislike", 0), "读过": fb.get("seen", 0) + fb.get("seen_like", 0), "读过且喜欢": fb.get("seen_like", 0)},
        "items": [
            {"no": it["pos"], "title": it["core_title"], "authors": it["authors"], "tags": it["tags"][:8],
             "parodies": it["parodies"], "why": it["reason"].get("text", ""),
             "route": it["reason"].get("route_name", ""),
             "your_reaction": {"want": "心动", "skip": "略过", "dislike": "不喜欢", "seen": "读过", "seen_like": "读过，喜欢"}.get(it["feedback"] or "", "")}
            for it in b["items"]
        ],
    }


def batch_prompt(ev: dict) -> str:
    return (
        "下面是一个本地漫画推荐系统给用户生成的一卷推荐，以及从他收藏里统计出的口味。请写一段“卷首语”：\n"
        "1. 两三句话概括他的口味（高频作者、题材、原作），说得具体些；\n"
        "2. 从这一卷里挑 3 本最值得先翻的，各用一句话说为什么（引用编号 No.）；\n"
        "3. 指出 1 本可能是意外之喜或可能踩雷的，说明理由；\n"
        "4. 若他已经表态过，顺便说说从这些表态里看出的倾向。\n"
        f"总长 300 字以内。{STYLE}\n\n" + json.dumps(ev, ensure_ascii=False, indent=1)
    )


def item_prompt(ev: dict, question: str = "") -> str:
    return (
        "下面是本地推荐系统给用户的一本推荐，以及它的匹配证据和用户最接近的几本收藏。"
        "比较这本与那几本收藏在题材、角色、作者上的共同点和差异，说明用户可能喜欢或不喜欢它的理由。"
        f"200 字以内。{STYLE}\n\n" + json.dumps(ev, ensure_ascii=False, indent=1)
        + (f"\n\n用户追问：{question}" if question else "")
    )


def analyze_batch(db: DB, batch_id: int, engine: str | None = None) -> dict:
    ev = batch_evidence(db, batch_id)
    text, used = run(batch_prompt(ev), engine)
    db.kv_set(f"analysis:batch:{batch_id}", {"text": text, "engine": used})
    return {"text": text, "engine": used, "engine_name": ENGINE_NAMES[used]}


def analyze_item(db: DB, batch_id: int, pos: int, question: str = "", engine: str | None = None) -> dict:
    ev = evidence(db, batch_id, pos)
    if not ev:
        raise AnalyzeError("找不到这张卡")
    ev = {**ev, "candidate": {**ev["candidate"], "core_title": parse_title(ev["candidate"]["title"]).core}}
    text, used = run(item_prompt(ev, question), engine)
    return {"text": text, "engine": used, "engine_name": ENGINE_NAMES[used]}
