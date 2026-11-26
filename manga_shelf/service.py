"""网页和 MCP 共用的服务层：同一份数据库、同一套推荐证据。"""
from __future__ import annotations

import logging
import subprocess
import sys
import threading
import time
import traceback
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import net
from .config import data_dir, load_settings, save_settings
from .db import DB
from .explain import evidence, llm_configured, llm_explanation, local_explanation
from .importer import import_favorites
from .ranker import describe_weights
from .recommend import batch_view, generate_batch
from .sources import SITES, SiteError, get_adapter

LABELS = {"want": "心动", "skip": "略过", "dislike": "不喜欢", "seen": "读过", "seen_like": "读过·喜欢"}


log = logging.getLogger("honmei")


def setup_logging() -> Path:
    """运行日志写到数据目录的 app.log，出问题时可以直接看。"""
    path = data_dir() / "app.log"
    if not any(isinstance(h, RotatingFileHandler) for h in log.handlers):
        h = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    return path


class Jobs:
    """后台任务（导入、生成批次、网络检测、安装组件），网页轮询进度。"""

    def __init__(self):
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def start(self, kind: str, title: str, fn, quiet: bool = False) -> str:
        jid = uuid.uuid4().hex[:10]
        job = {"id": jid, "kind": kind, "title": title, "status": "running", "progress": "准备中…", "quiet": quiet,
               "done": None, "total": None, "result": None, "error": None,
               "started_at": time.time(), "phase_started_at": time.time(), "finished_at": None}
        with self.lock:
            # 同类任务只允许一个在跑
            for j in self.jobs.values():
                if j["kind"] == kind and j["status"] == "running":
                    return j["id"]
            self.jobs[jid] = job
        log.info("任务开始 %s %s", kind, jid)

        def progress(msg: str, done: int | None = None, total: int | None = None) -> None:
            # 换了阶段（总数变化）就重新计时，用来估算剩余时间
            if total != job["total"]:
                job["phase_started_at"] = time.time()
            job["progress"], job["done"], job["total"] = msg, done, total

        def run():
            try:
                job["result"] = fn(progress)
                job["status"] = "done"
                log.info("任务完成 %s %s 用时 %.0fs 结果 %s", kind, jid, time.time() - job["started_at"],
                         job["result"])
            except SiteError as e:
                job["status"], job["error"] = "error", str(e)
                log.warning("任务失败 %s %s：%s", kind, jid, e)
            except Exception as e:
                job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"
                log.error("任务异常 %s %s\n%s", kind, jid, traceback.format_exc())
            finally:
                job["finished_at"] = time.time()

        threading.Thread(target=run, daemon=True).start()
        return jid

    def get(self, jid: str) -> dict | None:
        return self.jobs.get(jid)

    def recent(self) -> list[dict]:
        """正在跑的、刚完成的（8 秒内）、出错的（10 分钟内）。"""
        now = time.time()
        out = []
        for j in self.jobs.values():
            age = now - (j["finished_at"] or now)
            if j.get("quiet") and j["status"] != "error":
                continue   # 后台自动做的事（启动时的网络检测）不打扰
            if j["status"] == "running" or (j["status"] == "done" and age < 8) or \
                    (j["status"] == "error" and age < 600):
                out.append(j)
        return out


class Service:
    def __init__(self, db: DB | None = None):
        self.db = db or DB()
        self.jobs = Jobs()
        self._adapters: dict = {}
        setup_logging()

    def adapter(self, site: str):
        if site not in self._adapters:
            self._adapters[site] = get_adapter(site, self.db)
        return self._adapters[site]

    def reset_adapters(self) -> None:
        self._adapters.clear()

    # ---- 状态 ----
    def status(self) -> dict:
        ranker = self.db.kv_get("ranker") or {}
        settings = load_settings()
        return {
            "demo": bool(settings.get("demo")),
            "sites": {
                s: {"name": n, "logged_in": self.adapter(s).logged_in(),
                    "favorites": self.db.collection_counts().get(s, 0),
                    "last_import": self.db.kv_get(f"import:{s}")}
                for s, n in SITES.items()
            },
            "batches": self.db.list_batches(30),
            "rec_mode": settings.get("rec_mode", "balance"),
            "ranker": {**ranker, "weights_text": describe_weights(ranker)},
            "calibrate": self.calibrate_state(),
            "clusters": self.db.kv_get("taste:clusters"),
            "eval": {k: v for k, v in (self.db.kv_get("eval") or {}).items() if k in ("at", "summary", "variants")},
            "feedback_counts": {
                LABELS[r["label"]]: r["n"]
                for r in self.db.q("SELECT label, COUNT(*) n FROM feedback GROUP BY label")
            },
            "llm": llm_configured(settings),
            "analyze": self._analyze_status(settings),
            "semantic": bool((settings.get("semantic") or {}).get("enabled")),
            "semantic_installed": self.semantic_installed(),
            "net": net.current(settings),
            "jobs": self.jobs.recent(),
            "data_dir": str(data_dir()),
        }

    # ---- 调音 ----
    def calibrate_next(self) -> dict:
        from .calibrate import pick_round
        return pick_round(self.db)

    def calibrate_rate(self, body: dict) -> dict:
        from .calibrate import rate
        lv = body.get("level")
        rate(self.db, body["site"], str(body["wid"]), None if lv is None else int(lv), body.get("reasons"),
             int(body.get("round") or 1), body.get("source") or "fav")
        return {"ok": True}

    def calibrate_summary(self, rno: int) -> dict:
        from .calibrate import round_summary
        r = round_summary(self.db, rno)
        self.db.kv_set("calibrate:last", {"round": rno, "at": time.time(), "batches": len(self.db.list_batches(999))})
        return r

    def calibrate_state(self) -> dict:
        from .calibrate import current_round
        last = self.db.kv_get("calibrate:last") or {}
        n_batches = len(self.db.list_batches(999))
        rated = self.db.q("SELECT COUNT(*) n FROM calibration WHERE level > 0")[0]["n"]
        has_favs = sum(self.db.collection_counts().values()) > 0
        # 首次导入后亮一次；之后每看完 3 卷亮一个小点
        dot = has_favs and (rated == 0 or n_batches - last.get("batches", 0) >= 3)
        return {"round": current_round(self.db), "rated": rated, "dot": bool(dot)}

    def start_eval(self) -> str:
        from .evaluate import EvalError, evaluate

        def run(p):
            p("把最新的一部分收藏藏起来，看新旧算法谁能把它们找回来")
            try:
                r = evaluate(self.db, self.adapter("jm"), p)
            except EvalError as e:
                raise SiteError(str(e))
            return {"summary": r["summary"]}
        return self.jobs.start("eval", "检验算法", run)

    def start_portrait(self, engine: str | None = None) -> str:
        from .analyzer import AnalyzeError
        from .portrait import generate

        def run(p):
            p("正在请侧写师读你的口味摘要（通常半分钟到两分钟）…")
            try:
                r = generate(self.db, engine)
            except AnalyzeError as e:
                raise SiteError(str(e))
            return {"engine_name": r["engine_name"], "ok": bool(r["data"])}
        return self.jobs.start("portrait", "口味画像", run)

    def _analyze_status(self, settings: dict) -> dict:
        from .analyzer import ENGINE_NAMES, available, pick_engine
        e = pick_engine(settings)
        return {"available": available(settings), "engine": e, "engine_name": ENGINE_NAMES.get(e or "", ""),
                "preference": (settings.get("analyze") or {}).get("engine", "auto")}

    # ---- 登录与导入 ----
    def login(self, site: str, username: str, password: str) -> str:
        return self.adapter(site).login(username, password)

    def logout(self, site: str) -> None:
        for k in ("token", "username", "password"):
            self.db.secret_set(site, k, None)
        self._adapters.pop(site, None)

    def start_import(self, site: str, max_pages: int | None = None) -> str:
        ad = self.adapter(site)

        def run(p):
            r = import_favorites(self.db, ad, p, max_pages=max_pages)
            # 导入完顺手做两件慢活：学跨站标签对照、取站内基准样本（失败不影响导入结果）
            from .taste import background_stale, learn_aliases, refresh_background
            try:
                learn_aliases(self.db)
                if background_stale(self.db):
                    refresh_background(self.db, self.adapter("jm"), p)
            except Exception as e:
                logging.getLogger("honmei").warning("导入后的基准样本没取全：%s", e)
            return r
        return self.jobs.start(f"import-{site}", f"导入{SITES.get(site, site)}收藏", run)

    def start_generate(self, mode: str | None = None) -> str:
        from .recommend import MODES
        if mode in MODES:
            save_settings({"rec_mode": mode})
        jm = self.adapter("jm")

        def run(p):
            bid = generate_batch(self.db, jm, p, mode=mode)
            return {"batch_id": bid, "vol": bid - self.db.batch_offset()}
        return self.jobs.start("generate", "开卷", run)

    def reset_batches(self) -> dict:
        self.db.reset_batches()
        return {"ok": True}

    # ---- 解读（可选：API / Codex / Claude Code） ----
    def start_analyze_batch(self, batch_id: int, engine: str | None = None) -> str:
        from .analyzer import AnalyzeError, analyze_batch

        def run(p):
            p("正在请解读者读这一卷（Codex / Claude 通常要半分钟到两分钟）…")
            try:
                return {"batch_id": batch_id, **analyze_batch(self.db, batch_id, engine)}
            except AnalyzeError as e:
                raise SiteError(str(e))
        return self.jobs.start(f"analyze-{batch_id}", f"解读 Vol.{batch_id}", run)

    def start_analyze_item(self, batch_id: int, pos: int, question: str = "", engine: str | None = None) -> str:
        from .analyzer import AnalyzeError, analyze_item

        def run(p):
            p(f"正在细读 Vol.{batch_id} 第 {pos} 本…")
            try:
                return {"batch_id": batch_id, "pos": pos, **analyze_item(self.db, batch_id, pos, question, engine)}
            except AnalyzeError as e:
                raise SiteError(str(e))
        return self.jobs.start(f"analyze-{batch_id}-{pos}", f"细读 Vol.{batch_id} · No.{pos}", run)

    # ---- 网络与组件 ----
    def start_net_check(self, quiet: bool = False, delay: float = 0) -> str:
        def run(p):
            if delay:
                time.sleep(delay)
            p("检测网络：直连、系统代理、本地代理端口…")
            res = net.detect()
            self.reset_adapters()
            return res
        return self.jobs.start("net", "检测网络", run, quiet=quiet)

    def warm_up(self, delay: float = 4) -> None:
        """启动后在后台预先算一遍口味（载入算法库、填好缓存），第一次点调音 / 报告 / 开卷会快很多。"""
        def run():
            time.sleep(delay)
            t = time.time()
            try:
                from .recommend import build_profile, build_taste
                prof = build_profile(self.db)
                if not prof.empty:
                    build_taste(self.db, prof)
                log.info("预热完成 %.1fs", time.time() - t)
            except Exception as e:
                log.warning("预热失败：%s", e)
        threading.Thread(target=run, daemon=True).start()

    def semantic_installed(self) -> bool:
        import importlib.util
        return importlib.util.find_spec("sentence_transformers") is not None

    def start_install_semantic(self) -> str:
        def pip(args: list[str]) -> bool:
            env = None
            proxy = net.effective_proxy()
            if proxy:
                import os
                env = {**os.environ, "HTTPS_PROXY": proxy, "HTTP_PROXY": proxy}
            r = subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *args],
                               capture_output=True, text=True, env=env)
            log.info("pip %s -> %s\n%s", args, r.returncode, (r.stdout + r.stderr)[-3000:])
            return r.returncode == 0

        def run(p):
            mirror = ["-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
            p("安装 PyTorch（CPU 版，约 200MB）…", 1, 3)
            if not pip(["torch", "--index-url", "https://download.pytorch.org/whl/cpu"]):
                if not pip(["torch", *mirror]):
                    raise SiteError("PyTorch 安装失败，详情见 data\\app.log")
            p("安装 sentence-transformers…", 2, 3)
            if not pip(["sentence-transformers>=2.6"]) and not pip(["sentence-transformers>=2.6", *mirror]):
                raise SiteError("sentence-transformers 安装失败，详情见 data\\app.log")
            p("下载语义模型（约 470MB，第一次较慢）…", 3, 3)
            import importlib
            importlib.invalidate_caches()
            from .semantic import Semantic
            s = load_settings()
            sem = Semantic(self.db, s["semantic"]["model"], net.hf_endpoint(s), net.effective_proxy(s))
            sem.model()
            save_settings({"semantic": {"enabled": True}})
            return {"ok": True}
        return self.jobs.start("semantic", "安装语义匹配", run)

    def generate_now(self, progress=None) -> int:
        return generate_batch(self.db, self.adapter("jm"), progress)

    # ---- 批次与反馈 ----
    def batch(self, batch_id: int | None = None) -> dict | None:
        if batch_id is None:
            bs = self.db.list_batches(1)
            if not bs:
                return None
            batch_id = bs[0]["id"]
        return batch_view(self.db, batch_id)

    def feedback(self, batch_id: int, pos: int, label: str | None) -> dict:
        if label is not None and label not in LABELS:
            raise ValueError("label 只能是 want / skip / dislike / seen / seen_like")
        it = next((x for x in self.db.batch_items(batch_id) if x["pos"] == pos), None)
        if not it:
            raise KeyError("找不到这张卡")
        self.db.set_feedback(it["site"], it["wid"], label, batch_id)
        return {"batch_id": batch_id, "pos": pos, "label": label}

    def explain(self, batch_id: int, pos: int, deep: bool = False, question: str = "") -> dict:
        ev = evidence(self.db, batch_id, pos)
        if not ev:
            raise KeyError("找不到这张卡")
        out = {"evidence": ev, "local": local_explanation(ev)}
        if deep:
            from .analyzer import analyze_item
            out["llm"] = analyze_item(self.db, batch_id, pos, question)["text"]
        return out

    # ---- 设置 ----
    def settings(self) -> dict:
        s = load_settings()
        s = {**s, "llm": {**s.get("llm", {}), "api_key": "已填写" if s.get("llm", {}).get("api_key") else ""}}
        return s

    def update_settings(self, patch: dict) -> dict:
        llm = patch.get("llm")
        if isinstance(llm, dict) and llm.get("api_key") == "已填写":
            llm.pop("api_key")
        save_settings(patch)
        self.reset_adapters()
        return self.settings()
