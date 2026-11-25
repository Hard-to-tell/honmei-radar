"""网络自动检测：不用手动填代理和镜像。

检测顺序：直连 → 系统代理 → 常见本地代理端口（Clash 7890/7897、v2rayN 10809 等）。
分别看哔咔接口、禁漫接口、HuggingFace 能不能连上，记住能用的方式。
用户在“高级”里手动填了代理时，以手动为准。
"""
from __future__ import annotations

import socket
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import requests

from .config import load_settings, save_settings

COMMON_PORTS = [7890, 7897, 10809, 10792, 7899, 8889, 1081, 8118, 20171, 33210]

TARGETS = {
    "pica": "https://picaapi.picacomic.com/",
    "jm": None,  # 运行时取 jmcomic 的接口域名
    "hf": "https://huggingface.co/api/models?limit=1",
    "hf_mirror": "https://hf-mirror.com/api/models?limit=1",
}

RECHECK_AFTER = 6 * 3600


def _jm_targets() -> list[str]:
    try:
        from jmcomic import JmModuleConfig
        return [f"https://{d}/" for d in list(JmModuleConfig.DOMAIN_API_LIST)[:3]]
    except Exception:
        return ["https://www.cdnhjk.net/"]


def system_proxy() -> str | None:
    p = urllib.request.getproxies()
    return p.get("https") or p.get("http")


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _reachable(url: str, proxy: str | None, timeout: float = 6) -> bool:
    px = {"http": proxy, "https": proxy} if proxy else {"http": None, "https": None}
    try:
        s = requests.Session()
        s.trust_env = False  # 只用指定的方式，不混用环境变量
        r = s.get(url, proxies=px, timeout=timeout, allow_redirects=False,
                  headers={"User-Agent": "Mozilla/5.0"})
        return r.status_code < 600
    except requests.RequestException:
        return False


def _ok_any(urls: list[str], proxy: str | None) -> bool:
    return any(_reachable(u, proxy) for u in urls)


def detect() -> dict:
    """检测一次并保存结果。返回 {"proxy": ..., "pica": bool, "jm": bool, "hf_endpoint": ...}"""
    candidates: list[tuple[str, str | None]] = [("直连", None)]
    sp = system_proxy()
    if sp:
        candidates.append(("系统代理", sp))
    for port in COMMON_PORTS:
        if _port_open(port):
            url = f"http://127.0.0.1:{port}"
            if url != sp:
                candidates.append((f"本地代理 {port}", url))

    jm_urls = _jm_targets()

    def probe(c):
        name, proxy = c
        return name, proxy, _reachable(TARGETS["pica"], proxy), _ok_any(jm_urls, proxy)

    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(probe, candidates))

    # 优先两站都通的；其次能通一个的；保持候选顺序（直连优先）
    best = None
    for r in results:
        if r[2] and r[3]:
            best = r
            break
    if best is None:
        for r in results:
            if r[2] or r[3]:
                best = r
                break
    name, proxy, pica_ok, jm_ok = best if best else ("直连", None, False, False)

    # 模型下载：官方能连就用官方，否则用国内镜像
    if _reachable(TARGETS["hf"], proxy):
        hf = "https://huggingface.co"
    elif _reachable(TARGETS["hf_mirror"], proxy) or _reachable(TARGETS["hf_mirror"], None):
        hf = "https://hf-mirror.com"
    else:
        hf = "https://hf-mirror.com"

    net = {"route": name, "proxy": proxy, "pica": pica_ok, "jm": jm_ok, "hf_endpoint": hf,
           "checked_at": time.time(), "tried": [r[0] for r in results]}
    save_settings({"net": net})
    return net


def current(settings: dict | None = None) -> dict:
    s = settings or load_settings()
    return s.get("net") or {}


def effective_proxy(settings: dict | None = None) -> str | None:
    """手动填写的代理优先；否则用自动检测到的；都没有则直连。"""
    s = settings or load_settings()
    if s.get("proxy"):
        return s["proxy"]
    return (s.get("net") or {}).get("proxy")


def hf_endpoint(settings: dict | None = None) -> str:
    s = settings or load_settings()
    manual = (s.get("semantic") or {}).get("hf_endpoint")
    if manual:
        return manual
    return (s.get("net") or {}).get("hf_endpoint") or "https://hf-mirror.com"


def needs_check(settings: dict | None = None) -> bool:
    n = current(settings)
    return not n or time.time() - n.get("checked_at", 0) > RECHECK_AFTER
