"""网络自动检测：不用手动填代理和镜像。

检测顺序：直连 → 系统代理 → 常见本地代理端口（Clash 7890/7897、v2rayN 10809 等）。
哔咔直连不通时，再试“分流”：用固定 IP 连官方接口（证书照常校验，只是绕开被污染的 DNS）。
禁漫接口域名由 jmcomic 库自动更新；网页链接的镜像域名从禁漫发布页取，挑一个能打开的。
分别看哔咔接口、禁漫接口、HuggingFace 能不能连上，记住能用的方式。
用户在“高级”里手动填了代理时，以手动为准。
"""
from __future__ import annotations

import socket
import ssl
import threading
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

PICA_HOST = "picaapi.picacomic.com"
# 哔咔官方客户端的分流地址（Cloudflare 节点），来自开源客户端 pica-rust 的默认列表
PICA_IPS = ["172.67.7.24", "172.67.194.19", "172.67.80.1", "104.21.235.3", "104.21.235.4",
            "104.20.180.50", "104.20.181.50", "104.22.64.159", "104.21.91.145"]
PICA_IMAGE_HOSTS = ["storage1.picacomic.com", "s2.picacomic.com", "s3.picacomic.com",
                    "storage-b.picacomic.com", "storage.tipatipa.xyz", "storage.wikawika.xyz"]
JM_WEB_DEFAULT = "18comic.vip"

# ---------- 分流：指定域名连到固定 IP ----------
_PINNED: dict[str, str] = {}
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo(host, *a, **kw):
    return _orig_getaddrinfo(_PINNED.get(host, host), *a, **kw)


def pin_host(host: str, ip: str | None) -> None:
    """之后对 host 的连接都走 ip（只影响本进程的 requests 等；TLS 仍按原域名校验证书）。"""
    if ip:
        _PINNED[host] = ip
        socket.getaddrinfo = _getaddrinfo
    else:
        _PINNED.pop(host, None)


def _tls_probe(ip: str, host: str, timeout: float = 5) -> bool:
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((ip, 443), timeout=timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=host) as s:
                s.settimeout(timeout)
                s.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\nUser-Agent: okhttp/3.8.1\r\nConnection: close\r\n\r\n".encode())
                return s.recv(12).startswith(b"HTTP/")
    except (OSError, ssl.SSLError):
        return False


def find_pica_ip() -> str | None:
    """并发试分流 IP，返回最先连通的一个。"""
    with ThreadPoolExecutor(max_workers=len(PICA_IPS)) as ex:
        for ip, ok in zip(PICA_IPS, ex.map(lambda ip: _tls_probe(ip, PICA_HOST), PICA_IPS)):
            if ok:
                return ip
    return None


def _with_timeout(fn, seconds: float):
    box: list = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    t.join(seconds)
    return box[0] if box else None


def find_jm_web(proxy: str | None) -> tuple[str | None, list[str]]:
    """从禁漫发布页取当前所有网页域名，返回第一个能打开的。"""
    def fetch():
        try:
            from jmcomic import JmModuleConfig
            return JmModuleConfig.get_html_domain_all()
        except Exception:
            return []
    doms = [d for d in (_with_timeout(fetch, 12) or []) if "/" not in d][:8]
    for d in doms + [JM_WEB_DEFAULT]:
        if _reachable(f"https://{d}/", proxy, timeout=5):
            return d, doms
    return None, doms


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
    # 哔咔：代理都不行、或者只能靠代理时，先看直连 + 分流行不行，能行就不用代理
    pica_ip = None
    direct = results[0]
    if not direct[2]:
        pica_ip = find_pica_ip()
        if pica_ip:
            results[0] = direct = (direct[0], None, True, direct[3])
            if direct[3] or best is None or not (best[2] and best[3]):
                best = direct
    name, proxy, pica_ok, jm_ok = best if best else ("直连", None, False, False)
    if proxy:
        pica_ip = None
    pin_host(PICA_HOST, pica_ip)
    jm_web, jm_web_all = find_jm_web(proxy)

    # 模型下载：官方能连就用官方，否则用国内镜像
    if _reachable(TARGETS["hf"], proxy):
        hf = "https://huggingface.co"
    elif _reachable(TARGETS["hf_mirror"], proxy) or _reachable(TARGETS["hf_mirror"], None):
        hf = "https://hf-mirror.com"
    else:
        hf = "https://hf-mirror.com"

    net = {"route": name + ("（哔咔走分流）" if pica_ip else ""), "proxy": proxy, "pica": pica_ok, "jm": jm_ok,
           "pica_ip": pica_ip, "jm_web": jm_web, "jm_web_all": jm_web_all, "hf_endpoint": hf,
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


def apply(settings: dict | None = None) -> None:
    """按上次检测结果，给哔咔接口装上分流（用代理时不需要）。"""
    s = settings or load_settings()
    n = s.get("net") or {}
    pin_host(PICA_HOST, None if effective_proxy(s) else n.get("pica_ip"))


def jm_web_domain(settings: dict | None = None) -> str:
    s = settings or load_settings()
    manual = (s.get("jm") or {}).get("web_domain") or JM_WEB_DEFAULT
    if manual != JM_WEB_DEFAULT:
        return manual
    return (s.get("net") or {}).get("jm_web") or manual


def hf_endpoint(settings: dict | None = None) -> str:
    s = settings or load_settings()
    manual = (s.get("semantic") or {}).get("hf_endpoint")
    if manual:
        return manual
    return (s.get("net") or {}).get("hf_endpoint") or "https://hf-mirror.com"


def needs_check(settings: dict | None = None) -> bool:
    n = current(settings)
    return not n or time.time() - n.get("checked_at", 0) > RECHECK_AFTER
