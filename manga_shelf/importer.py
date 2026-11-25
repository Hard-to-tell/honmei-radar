"""导入收藏：先读收藏列表（按收藏时间）并立即入库，再并发补齐每本的标签与简介。

补详情可以中断：已经补过的作品缓存 30 天，下次导入或生成推荐时只补缺的。
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .db import DB
from .sources import SiteError
from .sources.base import Progress, SiteAdapter

DETAIL_TTL = 30 * 24 * 3600  # 详情缓存 30 天
WORKERS = 4                  # 并发数（总速率仍受站点节流限制）
MAX_CONSECUTIVE_FAIL = 12    # 连续失败这么多次就停下，多半是网络断了


def save_related(db: DB, site: str, wid: str, related) -> None:
    db.kv_set(f"related:{site}:{wid}", [w.wid for w in related])
    for r in related:
        db.upsert_work(r)


def _report(progress: Progress | None, msg: str, done: int | None = None, total: int | None = None) -> None:
    if not progress:
        return
    try:
        progress(msg, done, total)
    except TypeError:
        progress(msg)


def missing_details(db: DB, site: str, wids: list[str]) -> list[str]:
    now = time.time()
    todo = []
    for wid in wids:
        r = db.q("SELECT detail_ok, fetched_at FROM works WHERE site=? AND wid=?", (site, wid))
        if not r or not r[0]["detail_ok"] or now - r[0]["fetched_at"] > DETAIL_TTL:
            todo.append(wid)
    return todo


def ensure_details(db: DB, adapter: SiteAdapter, wids: list[str], progress: Progress | None = None,
                   label: str = "") -> int:
    """为缺少详情的作品并发取详情。单本失败不影响其他作品。返回失败数。"""
    todo = missing_details(db, adapter.site, wids)
    if not todo:
        return 0
    failed = 0
    done = 0
    consecutive = 0
    lock = threading.Lock()
    stop = threading.Event()
    last_error = ""

    def one(wid: str):
        if stop.is_set():
            return wid, None, "stopped"
        try:
            return wid, adapter.fetch_detail(wid), None
        except SiteError as e:
            return wid, None, str(e)
        except Exception as e:  # 网络库的其他异常
            return wid, None, f"{type(e).__name__}: {e}"

    _report(progress, f"{label}补全标签与简介 0/{len(todo)}", 0, len(todo))
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(one, wid) for wid in todo]
        for fut in as_completed(futs):
            wid, res, err = fut.result()
            with lock:
                done += 1
                if res:
                    w, related = res
                    db.upsert_work(w)
                    if related:
                        save_related(db, adapter.site, w.wid, related)
                    consecutive = 0
                elif err != "stopped":
                    failed += 1
                    consecutive += 1
                    last_error = err or ""
                    if consecutive >= MAX_CONSECUTIVE_FAIL:
                        stop.set()
            _report(progress, f"{label}补全标签与简介 {done}/{len(todo)}", done, len(todo))
    if stop.is_set():
        raise SiteError(f"{label}连续 {MAX_CONSECUTIVE_FAIL} 本取不到详情，已暂停（已完成的会保留，"
                        f"重新导入会接着补）。最后一个错误：{last_error}")
    return failed


def import_favorites(db: DB, adapter: SiteAdapter, progress: Progress | None = None,
                     max_pages: int | None = None, with_details: bool = True) -> dict:
    label = f"{adapter.name}："

    def list_progress(msg, done=None, total=None):
        _report(progress, msg, done, total)

    works = adapter.fetch_favorites(list_progress, max_pages=max_pages)
    for w in works:
        db.upsert_work(w)
    # 列表读完就入库，书架上马上能看到收藏数
    db.replace_collection(adapter.site, [w.wid for w in works])
    db.kv_set(f"import:{adapter.site}", {"at": time.time(), "count": len(works), "detail_failed": None})
    failed = 0
    if with_details:
        failed = ensure_details(db, adapter, [w.wid for w in works], progress, label=label)
    db.kv_set(f"import:{adapter.site}", {"at": time.time(), "count": len(works), "detail_failed": failed})
    return {"site": adapter.site, "count": len(works), "detail_failed": failed}
