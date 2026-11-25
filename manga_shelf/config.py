"""本地配置：数据目录、站点域名、代理、语义模型开关等。

配置文件是数据目录下的 settings.json，网页“设置”里改的就是它。
"""
from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

DEFAULTS: dict = {
    # 每批推荐多少本
    "batch_size": 20,
    # 每批里留给“探索”（热门/新题材）的名额比例
    "explore_ratio": 0.15,
    # 同一作者在一批里最多出现几本
    "max_per_author": 2,
    # 手动代理，例如 "http://127.0.0.1:7890"；留空则自动检测（直连 / 系统代理 / 常见本地代理端口）
    "proxy": "",
    "jm": {
        # 原作链接用的网页域名，打不开时在设置里换一个
        "web_domain": "18comic.vip",
        # 接口域名，留空则由 jmcomic 库自动获取最新域名
        "api_domains": [],
    },
    "pica": {
        # 哔咔作品的网页链接模板，{id} 会被替换成作品 ID
        "web_url_template": "https://manhuabika.com/pcomicview/?cid={id}",
    },
    "semantic": {
        # 语义匹配默认关闭；打开后首次使用会下载约 470MB 的模型
        "enabled": False,
        "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        # 留空自动选择：官方能连用官方，否则用 hf-mirror.com
        "hf_endpoint": "",
    },
    "llm": {
        # 可选：OpenAI 兼容接口，仅用于“深入分析”，不配置也能完整使用
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    # 请求节流：同一站点两次请求的最小间隔（秒）
    "rate_interval": 0.15,
}


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LEGACY_DIR = Path.home() / ".manga-shelf"


def data_dir() -> Path:
    """数据目录：从源码运行时放在项目文件夹的 data/ 里；也可用环境变量 MANGA_SHELF_HOME 指定。"""
    env = os.environ.get("MANGA_SHELF_HOME")
    if env:
        p = Path(env)
    elif (PROJECT_ROOT / "pyproject.toml").exists():
        p = PROJECT_ROOT / "data"
        if not p.exists() and (LEGACY_DIR / "shelf.db").exists():
            # 旧版本的数据在用户目录下，第一次启动时搬过来（保留原文件）
            import shutil
            shutil.copytree(LEGACY_DIR, p)
    else:
        p = LEGACY_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def _merge(base: dict, override: dict) -> dict:
    out = deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    p = settings_path()
    if p.exists():
        try:
            return _merge(DEFAULTS, json.loads(p.read_text("utf-8")))
        except Exception:
            pass
    return deepcopy(DEFAULTS)


def save_settings(patch: dict) -> dict:
    """只把用户改过的项写进文件，默认值以后升级时还能生效。"""
    p = settings_path()
    raw: dict = {}
    if p.exists():
        try:
            raw = json.loads(p.read_text("utf-8"))
        except Exception:
            raw = {}
    raw = _merge(raw, patch)
    p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), "utf-8")
    return _merge(DEFAULTS, raw)


def proxies(settings: dict | None = None) -> dict | None:
    """给 requests 用的代理：手动 > 自动检测 > 系统默认。"""
    from .net import effective_proxy
    p = effective_proxy(settings or load_settings())
    if p:
        return {"http": p, "https": p}
    return None
