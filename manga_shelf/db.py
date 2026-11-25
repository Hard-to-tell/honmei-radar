"""SQLite 本地存储：作品、收藏、批次、反馈、凭据、向量缓存。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import data_dir
from .models import Work

SCHEMA = """
CREATE TABLE IF NOT EXISTS works (
    site TEXT NOT NULL,
    wid TEXT NOT NULL,
    title TEXT NOT NULL,
    authors TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    parodies TEXT NOT NULL DEFAULT '[]',
    circles TEXT NOT NULL DEFAULT '[]',
    groups_ TEXT NOT NULL DEFAULT '[]',
    category TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    cover TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    popularity REAL NOT NULL DEFAULT 0,
    detail_ok INTEGER NOT NULL DEFAULT 0,
    fetched_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (site, wid)
);
CREATE TABLE IF NOT EXISTS collections (
    site TEXT NOT NULL,
    wid TEXT NOT NULL,
    rank INTEGER NOT NULL,           -- 0 = 最新收藏
    imported_at REAL NOT NULL,
    PRIMARY KEY (site, wid)
);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    engine TEXT NOT NULL,            -- 打分方式：baseline / baseline+semantic / personal
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS batch_items (
    batch_id INTEGER NOT NULL,
    pos INTEGER NOT NULL,            -- 固定次序，从 1 开始
    site TEXT NOT NULL,
    wid TEXT NOT NULL,
    score REAL NOT NULL,
    features TEXT NOT NULL,          -- 当时的特征（训练用，反馈前的值）
    reason TEXT NOT NULL,            -- 当时的依据（解释用）
    route TEXT NOT NULL,             -- 候选来源：author / tag / related / explore
    PRIMARY KEY (batch_id, pos)
);
CREATE TABLE IF NOT EXISTS feedback (
    site TEXT NOT NULL,
    wid TEXT NOT NULL,
    label TEXT NOT NULL,             -- want / skip / dislike / seen
    batch_id INTEGER,
    created_at REAL NOT NULL,
    PRIMARY KEY (site, wid)
);
CREATE TABLE IF NOT EXISTS secrets (
    site TEXT NOT NULL,
    k TEXT NOT NULL,
    v TEXT NOT NULL,
    PRIMARY KEY (site, k)
);
CREATE TABLE IF NOT EXISTS embeddings (
    site TEXT NOT NULL,
    wid TEXT NOT NULL,
    model TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vec BLOB NOT NULL,
    PRIMARY KEY (site, wid, model)
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calibration (
    site TEXT NOT NULL,
    wid TEXT NOT NULL,
    level INTEGER NOT NULL,          -- 4 本命 / 3 喜欢 / 2 一般 / 1 其实不太喜欢 / 0 跳过
    reasons TEXT NOT NULL DEFAULT '[]',  -- 实用 / 艺术 / 画风 / 题材 / 剧情
    round INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'fav',  -- fav 收藏 / rec 推荐 / random 站内随机
    at REAL NOT NULL,
    PRIMARY KEY (site, wid)
);
"""


class DB:
    def __init__(self, path: Path | str | None = None):
        self.path = str(path or (data_dir() / "shelf.db"))
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------- 通用 ----------
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> None:
        with self._lock:
            self.conn.execute(sql, args)
            self.conn.commit()

    def kv_get(self, k: str, default=None):
        r = self.q("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(r[0]["v"]) if r else default

    def kv_set(self, k: str, v) -> None:
        self.x("INSERT OR REPLACE INTO kv(k, v) VALUES (?, ?)", (k, json.dumps(v, ensure_ascii=False)))

    # ---------- 作品 ----------
    def upsert_work(self, w: Work) -> None:
        with self._lock:
            old = self.conn.execute(
                "SELECT detail_ok FROM works WHERE site=? AND wid=?", (w.site, w.wid)
            ).fetchone()
            if old and old["detail_ok"] and not w.detail_ok:
                # 已有详情时，列表页的简略信息只补封面和标题，不覆盖标签
                self.conn.execute(
                    "UPDATE works SET cover=COALESCE(NULLIF(?, ''), cover), url=COALESCE(NULLIF(?, ''), url) "
                    "WHERE site=? AND wid=?",
                    (w.cover, w.url, w.site, w.wid),
                )
            else:
                self.conn.execute(
                    "INSERT OR REPLACE INTO works(site, wid, title, authors, tags, parodies, circles, groups_, "
                    "category, description, cover, url, popularity, detail_ok, fetched_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        w.site, w.wid, w.title,
                        json.dumps(w.authors, ensure_ascii=False),
                        json.dumps(w.tags, ensure_ascii=False),
                        json.dumps(w.parodies, ensure_ascii=False),
                        json.dumps(w.circles, ensure_ascii=False),
                        json.dumps(w.groups, ensure_ascii=False),
                        w.category, w.description, w.cover, w.url, w.popularity,
                        1 if w.detail_ok else 0, time.time(),
                    ),
                )
            self.conn.commit()

    @staticmethod
    def _row_to_work(r: sqlite3.Row) -> Work:
        return Work(
            site=r["site"], wid=r["wid"], title=r["title"],
            authors=json.loads(r["authors"]), tags=json.loads(r["tags"]),
            parodies=json.loads(r["parodies"]), circles=json.loads(r["circles"]),
            groups=json.loads(r["groups_"]), category=r["category"],
            description=r["description"], cover=r["cover"], url=r["url"],
            popularity=r["popularity"], detail_ok=bool(r["detail_ok"]),
        )

    def get_work(self, site: str, wid: str) -> Work | None:
        r = self.q("SELECT * FROM works WHERE site=? AND wid=?", (site, str(wid)))
        return self._row_to_work(r[0]) if r else None

    def get_works(self, keys: list[tuple[str, str]]) -> dict[tuple[str, str], Work]:
        out = {}
        for site, wid in keys:
            w = self.get_work(site, wid)
            if w:
                out[(site, wid)] = w
        return out

    # ---------- 收藏 ----------
    def replace_collection(self, site: str, ordered_wids: list[str]) -> None:
        now = time.time()
        with self._lock:
            self.conn.execute("DELETE FROM collections WHERE site=?", (site,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO collections(site, wid, rank, imported_at) VALUES (?,?,?,?)",
                [(site, str(w), i, now) for i, w in enumerate(ordered_wids)],
            )
            self.conn.commit()

    def collection(self, site: str | None = None) -> list[Work]:
        sql = ("SELECT w.*, c.rank FROM collections c JOIN works w ON w.site=c.site AND w.wid=c.wid")
        args: tuple = ()
        if site:
            sql += " WHERE c.site=?"
            args = (site,)
        sql += " ORDER BY c.site, c.rank"
        return [self._row_to_work(r) for r in self.q(sql, args)]

    def collection_counts(self) -> dict[str, int]:
        return {r["site"]: r["n"] for r in self.q("SELECT site, COUNT(*) n FROM collections GROUP BY site")}

    # ---------- 批次 ----------
    def create_batch(self, engine: str, items: list[dict], meta: dict | None = None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO batches(created_at, engine, meta) VALUES (?,?,?)",
                (time.time(), engine, json.dumps(meta or {}, ensure_ascii=False)),
            )
            bid = cur.lastrowid
            self.conn.executemany(
                "INSERT INTO batch_items(batch_id, pos, site, wid, score, features, reason, route) "
                "VALUES (?,?,?,?,?,?,?,?)",
                [
                    (bid, i + 1, it["site"], it["wid"], it["score"],
                     json.dumps(it["features"], ensure_ascii=False),
                     json.dumps(it["reason"], ensure_ascii=False), it["route"])
                    for i, it in enumerate(items)
                ],
            )
            self.conn.commit()
            return bid

    # 重置开卷记录：不删数据（心动 / 略过要靠它们训练），只记下“从哪一卷之后算起”，之后的卷从 Vol.1 重新编号
    def batch_offset(self) -> int:
        return int((self.kv_get("batches:reset") or {}).get("after", 0))

    def reset_batches(self) -> int:
        r = self.q("SELECT MAX(id) m FROM batches")
        after = int(r[0]["m"] or 0)
        self.kv_set("batches:reset", {"after": after, "at": time.time()})
        return after

    def list_batches(self, limit: int = 50) -> list[dict]:
        off = self.batch_offset()
        rows = self.q(
            "SELECT b.id, b.created_at, b.engine, b.meta, COUNT(i.pos) n FROM batches b "
            "LEFT JOIN batch_items i ON i.batch_id=b.id WHERE b.id > ? GROUP BY b.id ORDER BY b.id DESC LIMIT ?",
            (off, limit),
        )
        return [
            {"id": r["id"], "vol": r["id"] - off, "created_at": r["created_at"], "engine": r["engine"],
             "meta": json.loads(r["meta"]), "count": r["n"]}
            for r in rows
        ]

    def batch_items(self, batch_id: int) -> list[dict]:
        rows = self.q("SELECT * FROM batch_items WHERE batch_id=? ORDER BY pos", (batch_id,))
        return [
            {"batch_id": r["batch_id"], "pos": r["pos"], "site": r["site"], "wid": r["wid"],
             "score": r["score"], "features": json.loads(r["features"]),
             "reason": json.loads(r["reason"]), "route": r["route"]}
            for r in rows
        ]

    def batch_meta(self, batch_id: int) -> dict | None:
        off = self.batch_offset()
        r = self.q("SELECT * FROM batches WHERE id=? AND id > ?", (batch_id, off))
        if not r:
            return None
        return {"id": r[0]["id"], "vol": r[0]["id"] - off, "created_at": r[0]["created_at"], "engine": r[0]["engine"],
                "meta": json.loads(r[0]["meta"])}

    def shown_keys(self) -> set[tuple[str, str]]:
        return {(r["site"], r["wid"]) for r in self.q("SELECT DISTINCT site, wid FROM batch_items")}

    # ---------- 反馈 ----------
    def set_feedback(self, site: str, wid: str, label: str | None, batch_id: int | None) -> None:
        if label is None:
            self.x("DELETE FROM feedback WHERE site=? AND wid=?", (site, wid))
        else:
            self.x(
                "INSERT OR REPLACE INTO feedback(site, wid, label, batch_id, created_at) VALUES (?,?,?,?,?)",
                (site, wid, label, batch_id, time.time()),
            )

    def feedback_map(self) -> dict[tuple[str, str], dict]:
        return {
            (r["site"], r["wid"]): {"label": r["label"], "batch_id": r["batch_id"], "created_at": r["created_at"]}
            for r in self.q("SELECT * FROM feedback")
        }

    # ---------- 调音 ----------
    def set_calibration(self, site: str, wid: str, level: int | None, reasons: list[str] | None = None,
                        rnd: int = 1, source: str = "fav") -> None:
        if level is None:
            self.x("DELETE FROM calibration WHERE site=? AND wid=?", (site, wid))
        else:
            self.x("INSERT OR REPLACE INTO calibration(site, wid, level, reasons, round, source, at) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (site, wid, int(level), json.dumps(reasons or [], ensure_ascii=False), rnd, source, time.time()))

    def calibration_map(self) -> dict[tuple[str, str], dict]:
        return {
            (r["site"], r["wid"]): {"level": r["level"], "reasons": json.loads(r["reasons"] or "[]"),
                                    "round": r["round"], "source": r["source"], "at": r["at"]}
            for r in self.q("SELECT * FROM calibration")
        }

    # ---------- 凭据 ----------
    def secret_get(self, site: str, k: str) -> str | None:
        r = self.q("SELECT v FROM secrets WHERE site=? AND k=?", (site, k))
        return r[0]["v"] if r else None

    def secret_set(self, site: str, k: str, v: str | None) -> None:
        if v is None:
            self.x("DELETE FROM secrets WHERE site=? AND k=?", (site, k))
        else:
            self.x("INSERT OR REPLACE INTO secrets(site, k, v) VALUES (?,?,?)", (site, k, v))
