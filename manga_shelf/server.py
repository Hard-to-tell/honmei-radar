"""本地书架网页服务（只监听 127.0.0.1）。"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

import requests
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response

from .config import data_dir, load_settings, proxies
from .service import Service
from .sources import SiteError

STATIC = Path(__file__).parent / "static"


def create_app(service: Service | None = None) -> FastAPI:
    svc = service or Service()
    app = FastAPI(title="本命 HONMEI", docs_url=None, redoc_url=None)
    app.state.svc = svc

    @app.on_event("startup")
    def _auto_net_check():
        # 启动时自动检测一次网络（6 小时内检测过就跳过），不用手动填代理
        from .net import needs_check
        if needs_check() and not load_settings().get("demo"):
            svc.start_net_check()

    @app.get("/", response_class=HTMLResponse)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/status")
    def status():
        return svc.status()

    @app.post("/api/login/{site}")
    def login(site: str, body: dict = Body(...)):
        try:
            svc.login(site, body.get("username", "").strip(), body.get("password", ""))
        except SiteError as e:
            raise HTTPException(400, str(e))
        return {"ok": True}

    @app.post("/api/logout/{site}")
    def logout(site: str):
        svc.logout(site)
        return {"ok": True}

    @app.post("/api/import/{site}")
    def start_import(site: str, body: dict = Body(default={})):
        return {"job": svc.start_import(site, body.get("max_pages"))}

    @app.post("/api/batches")
    def start_generate():
        return {"job": svc.start_generate()}

    @app.get("/api/jobs")
    def jobs():
        return svc.jobs.recent()

    @app.post("/api/net/check")
    def net_check():
        return {"job": svc.start_net_check()}

    @app.post("/api/semantic/install")
    def semantic_install():
        return {"job": svc.start_install_semantic()}

    @app.post("/api/analyze/batch/{bid}")
    def analyze_batch(bid: int, body: dict = Body(default={})):
        return {"job": svc.start_analyze_batch(bid, body.get("engine") or None)}

    @app.get("/api/analysis/batch/{bid}")
    def analysis_batch(bid: int):
        return svc.db.kv_get(f"analysis:batch:{bid}") or {}

    @app.post("/api/analyze/item/{bid}/{pos}")
    def analyze_item(bid: int, pos: int, body: dict = Body(default={})):
        return {"job": svc.start_analyze_item(bid, pos, body.get("q", ""), body.get("engine") or None)}

    @app.get("/report", response_class=HTMLResponse)
    def report_page():
        return FileResponse(STATIC / "report.html")

    @app.get("/api/insights")
    def insights():
        from .insights import summary
        return summary(svc.db)

    @app.post("/api/report/hidden")
    def report_hidden(body: dict = Body(...)):
        from .insights import set_hidden
        return {"hidden_tags": set_hidden(svc.db, str(body.get("tag") or ""), bool(body.get("hidden", True)))}

    @app.get("/api/favorites/search")
    def favorites_search(q: str = ""):
        from .insights import search_favorites
        return {"items": search_favorites(svc.db, q)}

    @app.post("/api/honmei")
    def honmei(body: dict = Body(...)):
        """手动添加 / 去掉本命（记成调音里的“本命”评分）。"""
        if not body.get("site") or not body.get("wid"):
            raise HTTPException(400, "缺少作品")
        on = bool(body.get("on", True))
        svc.db.set_calibration(body["site"], str(body["wid"]), 4 if on else None, [], 0, "fav")
        return {"ok": True}

    @app.get("/api/calibrate/next")
    def calibrate_next():
        return svc.calibrate_next()

    @app.post("/api/calibrate")
    def calibrate_rate(body: dict = Body(...)):
        if not body.get("site") or not body.get("wid"):
            raise HTTPException(400, "缺少作品")
        return svc.calibrate_rate(body)

    @app.get("/api/calibrate/summary/{rno}")
    def calibrate_summary(rno: int):
        return svc.calibrate_summary(rno)

    @app.post("/api/eval")
    def eval_start():
        return {"job": svc.start_eval()}

    @app.get("/api/eval")
    def eval_result():
        return svc.db.kv_get("eval") or {}

    @app.get("/api/portrait/prompt")
    def portrait_prompt():
        from .portrait import full_prompt
        t = full_prompt(svc.db)
        return {"prompt": t, "chars": len(t)}

    @app.post("/api/portrait/import")
    def portrait_import(body: dict = Body(...)):
        from .portrait import save_external
        t = (body.get("text") or "").strip()
        if not t:
            raise HTTPException(400, "没有内容")
        return save_external(svc.db, t, body.get("engine_name") or "外部 AI")

    @app.get("/api/portrait")
    def portrait():
        from .portrait import import_inbox
        import_inbox(svc.db)
        h = svc.db.kv_get("portraits", [])
        return h[0] if h else {}

    @app.post("/api/portrait")
    def portrait_start(body: dict = Body(default={})):
        return {"job": svc.start_portrait(body.get("engine") or None)}

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        j = svc.jobs.get(jid)
        if not j:
            raise HTTPException(404, "任务不存在")
        return j

    @app.get("/api/batches")
    def batches():
        return svc.db.list_batches(100)

    @app.get("/api/batches/latest")
    def latest():
        return svc.batch(None)

    @app.get("/api/batches/{bid}")
    def batch(bid: int):
        b = svc.batch(bid)
        if not b:
            raise HTTPException(404, "批次不存在")
        return b

    @app.post("/api/feedback")
    def feedback(body: dict = Body(...)):
        try:
            return svc.feedback(int(body["batch_id"]), int(body["pos"]), body.get("label"))
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.get("/api/explain/{bid}/{pos}")
    def explain(bid: int, pos: int, deep: bool = False, q: str = ""):
        try:
            return svc.explain(bid, pos, deep, q)
        except KeyError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(400, f"分析失败：{e}")

    @app.get("/api/settings")
    def get_settings():
        return svc.settings()

    @app.post("/api/settings")
    def set_settings(body: dict = Body(...)):
        return svc.update_settings(body)

    @app.get("/api/cover/{site}/{wid}")
    def cover(site: str, wid: str):
        """封面经本地转发并缓存，避开防盗链和域名变动。"""
        cache = data_dir() / "covers"
        cache.mkdir(exist_ok=True)
        safe = hashlib.md5(f"{site}:{wid}".encode()).hexdigest()
        f = cache / f"{safe}.img"
        if f.exists() and f.stat().st_size > 0:
            return Response(f.read_bytes(), media_type="image/jpeg",
                            headers={"Cache-Control": "max-age=604800"})
        w = svc.db.get_work(site, wid)
        if not w or load_settings().get("demo"):
            raise HTTPException(404)
        urls = [w.cover] if w.cover else []
        if site == "jm":
            try:
                from jmcomic import JmcomicText, JmModuleConfig
                doms = list(JmModuleConfig.DOMAIN_IMAGE_LIST)
                random.shuffle(doms)
                urls += [JmcomicText.get_album_cover_url(wid, d, "_3x4") for d in doms[:3]]
            except Exception:
                pass
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                 "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
        for u in dict.fromkeys(urls):
            try:
                r = requests.get(u, headers=headers, timeout=12, proxies=proxies())
                if r.ok and r.content and r.headers.get("content-type", "").startswith("image"):
                    f.write_bytes(r.content)
                    return Response(r.content, media_type=r.headers["content-type"],
                                    headers={"Cache-Control": "max-age=604800"})
            except requests.RequestException:
                continue
        raise HTTPException(404, "封面获取失败")

    return app
