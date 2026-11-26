import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

_HOME = tempfile.mkdtemp(prefix="manga-shelf-test-")
os.environ["MANGA_SHELF_HOME"] = _HOME
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from manga_shelf.config import save_settings  # noqa: E402
from manga_shelf.db import DB  # noqa: E402
from manga_shelf.normalize import norm_tags, parse_title, title_key  # noqa: E402
from manga_shelf.sources.pica import sign_headers, thumb_url  # noqa: E402


@pytest.fixture()
def svc(tmp_path):
    save_settings({"demo": True, "semantic": {"enabled": False}, "batch_size": 12})
    from manga_shelf.service import Service
    return Service(DB(tmp_path / "t.db"))


def test_parse_doujin_title():
    pt = parse_title("[無邪氣漢化組] (C99) [紺色果實 (紺菓)] サレンの樂しい夢 (プリンセスコネクト!Re:Dive) [中國翻譯]")
    assert pt.core == "サレンの樂しい夢"
    assert pt.artists == ["紺菓"] and pt.circles == ["紺色果實"]
    assert pt.parodies == ["プリンセスコネクト!Re:Dive"]
    assert pt.groups == ["無邪氣漢化組"]


def test_parse_flags_and_volume():
    pt = parse_title("[been]The illusion of lies（1）[中國語][無修正][全彩]")
    assert pt.artists == ["been"] and pt.parodies == []
    assert set(pt.flags) == {"全彩", "无修正"}


def test_cross_site_title_key_ignores_brackets_and_script():
    a = title_key("[汉化组] [社团 (作者)] 夏日的約定 (原神) [中國翻譯]")
    b = title_key("[社團 (作者)] 夏日的约定 (原神) [DL版]")
    assert a == b


def test_tag_normalization():
    assert norm_tags(["中文", "長篇", "寝取られ", "NTR"]) == ["长篇", "ntr"]


def test_pica_signature_shape():
    h = sign_headers("users/favourite?s=dd&page=1", "GET", now=1700000000, nonce="a" * 32)
    assert len(h["signature"]) == 64 and h["time"] == "1700000000"
    assert thumb_url({"fileServer": "https://storage1.picacomic.com", "path": "x/y.jpg"}) == \
        "https://storage1.picacomic.com/static/x/y.jpg"


def test_end_to_end_demo(svc):
    from manga_shelf.importer import import_favorites
    r1 = import_favorites(svc.db, svc.adapter("pica"))
    r2 = import_favorites(svc.db, svc.adapter("jm"))
    assert r1["count"] > 0 and r2["count"] > 0

    bid = svc.generate_now()
    b = svc.batch(bid)
    assert len(b["items"]) == 12
    jm_favs = {w.wid for w in svc.db.collection("jm")}
    pica_keys = {title_key(w.title) for w in svc.db.collection("pica")}
    for it in b["items"]:
        assert it["wid"] not in jm_favs
        assert title_key(it["title"]) not in pica_keys
        assert it["reason"]["text"]
        assert it["url"]
    # 每批作者去重
    from collections import Counter
    c = Counter(it["authors"][0] for it in b["items"] if it["authors"])
    assert max(c.values()) <= 2

    # 反馈与撤销
    svc.feedback(bid, 1, "want")
    svc.feedback(bid, 2, "skip")
    svc.feedback(bid, 3, "seen")
    svc.feedback(bid, 3, None)
    fb = svc.db.feedback_map()
    assert len(fb) == 2

    # 重启后批次和反馈仍在
    db2 = DB(svc.db.path)
    assert db2.batch_items(bid)[0]["features"]
    assert len(db2.feedback_map()) == 2

    # 解释与当时的卡一致
    ex = svc.explain(bid, 1)
    assert ex["evidence"]["pos"] == 1 and "Vol." in ex["local"]

    # 新一批不重复已反馈的作品
    bid2 = svc.generate_now()
    keys2 = {(it["site"], it["wid"]) for it in svc.batch(bid2)["items"]}
    assert not (keys2 & set(fb.keys()))


def test_ranker_gating(svc):
    from manga_shelf.importer import import_favorites
    from manga_shelf.ranker import train
    import_favorites(svc.db, svc.adapter("jm"))
    # 只有一类反馈：保持基线
    bid = svc.generate_now()
    svc.feedback(bid, 1, "want")
    st = train(svc.db, False)
    assert st["mode"] == "baseline"
    # 多个批次、多位作者的正负反馈：至少进入试训
    for _ in range(3):
        bid = svc.generate_now()
        items = svc.batch(bid)["items"]
        for it in items:
            label = "want" if "纯爱" in it["tags"] else "skip"
            svc.feedback(bid, it["pos"], label)
    st = train(svc.db, False)
    assert st["mode"] in ("shadow", "personal")
    assert st["weights"] is not None


def test_http_api(svc):
    from fastapi.testclient import TestClient
    from manga_shelf.server import create_app
    c = TestClient(create_app(svc))
    assert c.get("/").status_code == 200
    assert c.post("/api/login/pica", json={"username": "a", "password": "b"}).status_code == 200
    s = c.get("/api/status").json()
    assert "pica" in s["sites"]
    import time
    jid = c.post("/api/import/jm", json={}).json()["job"]
    for _ in range(100):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done", j
    jid = c.post("/api/batches").json()["job"]
    for _ in range(200):
        j = c.get(f"/api/jobs/{jid}").json()
        if j["status"] != "running":
            break
        time.sleep(0.05)
    assert j["status"] == "done", j
    b = c.get("/api/batches/latest").json()
    assert b["items"]
    r = c.post("/api/feedback", json={"batch_id": b["id"], "pos": 1, "label": "want"})
    assert r.status_code == 200
    assert c.get(f"/api/explain/{b['id']}/1").status_code == 200


def test_mcp_tools_register(svc):
    from manga_shelf.mcp_server import build
    server = build(svc)
    assert server is not None


def test_insights_and_portrait(svc, monkeypatch):
    import json

    from fastapi.testclient import TestClient
    from manga_shelf import analyzer, portrait
    from manga_shelf.importer import import_favorites
    from manga_shelf.insights import llm_digest, summary
    from manga_shelf.server import create_app
    import_favorites(svc.db, svc.adapter("pica"))
    import_favorites(svc.db, svc.adapter("jm"))
    s = summary(svc.db)
    assert s["total"] > 0 and s["top_tags"] and s["dimensions"] and s["axes"]
    # 调音里的本命一定出现在提示词里；账号凭据永远不在
    fav = svc.db.collection("jm")[5]
    svc.db.set_calibration("jm", fav.wid, 4, ["剧情"], 1, "fav")
    svc.db.secret_set("jm", "password", "SECRET-PW")
    prompt = portrait.build_prompt(llm_digest(summary(svc.db), svc.db))
    assert parse_title(fav.title).core in prompt and "SECRET-PW" not in prompt
    assert s["clusters"] and s["clusters"][0]["works"]

    fake = {"title": "测试称号", "one_liner": "一句话", "keywords": ["a"], "serious": [], "bold": [], "drift": "", "next": ""}
    monkeypatch.setattr(analyzer, "run", lambda p, e=None: ("前言\n" + json.dumps(fake, ensure_ascii=False), "api"))
    out = portrait.generate(svc.db)
    assert out["data"]["title"] == "测试称号"
    c = TestClient(create_app(svc))
    assert c.get("/report").status_code == 200
    assert c.get("/api/insights").json()["total"] == s["total"]
    assert c.get("/api/portrait").json()["data"]["one_liner"] == "一句话"


def test_v03_calibration_and_eval(svc):
    from manga_shelf.evaluate import EvalError, evaluate
    from manga_shelf.importer import import_favorites
    from manga_shelf.recommend import _quotas
    from manga_shelf.taste import background_works, refresh_background
    import random
    import_favorites(svc.db, svc.adapter("pica"))
    import_favorites(svc.db, svc.adapter("jm"))
    assert refresh_background(svc.db, svc.adapter("jm"), seed=1) > 40
    assert background_works(svc.db, ("latest", "random"))
    # 名额按开平方分：大簇不会吃掉整卷，静音簇最多 1 本
    q = _quotas(17, [{"share": .6}, {"share": .3}, {"share": .1, "muted": True}], random.Random(0))
    assert q[0] < 12 and q[1] >= 4 and q[2] <= 1
    r = svc.calibrate_next()
    assert r["round"] == 1 and len(r["items"]) == 5
    assert len({it["cluster"] for it in r["items"]}) >= 3     # 第 1 轮每簇一本
    for it in r["items"]:
        svc.calibrate_rate({"site": it["site"], "wid": it["wid"], "level": 4, "reasons": ["剧情"],
                            "round": 1, "source": it["source"]})
    sm = svc.calibrate_summary(1)
    assert sm["text"] and all(it["level"] == 4 and "艺术" in it["reasons"] for it in sm["items"])
    r2 = svc.calibrate_next()
    assert r2["round"] == 2 and not ({(i["site"], i["wid"]) for i in r2["items"]} &
                                     {(i["site"], i["wid"]) for i in r["items"]})
    bid = svc.generate_now()
    assert len(svc.batch(bid)["items"]) == 12
    try:
        out = evaluate(svc.db, svc.adapter("jm"))
        assert set(out["variants"]) == {"v0.3", "v0.4"} and out["summary"]
    except EvalError:
        pass


def test_dedup_versions():
    from manga_shelf.normalize import dedup_keys
    same = ["(C999) [テスト堂 (山田)] 星降る夜の約束 (オリジナル)",
            "[某某漢化組] [某某去碼] [テスト堂 (山田)] 星降る夜の約束 (オリジナル) [DL版]"]
    assert dedup_keys(same[0]) & dedup_keys(same[1])
    zh = ["图书馆里的那个她 [某某漢化組] [サンプル作家] 図書室のあの子は [中國翻譯] [DL版]",
          "[某某漢化組] [サンプル作家] 図書室のあの子は [中國翻譯]"]
    assert dedup_keys(zh[0]) & dedup_keys(zh[1])
    assert not (dedup_keys("[サンプル作家] いつまでも隣にいるから 前編") & dedup_keys("[サンプル作家] いつまでも隣にいるから 後編"))


def test_seen_like_counts_as_positive(svc):
    from manga_shelf.importer import import_favorites
    from manga_shelf.ranker import training_rows
    import_favorites(svc.db, svc.adapter("jm"))
    bid = svc.generate_now()
    svc.feedback(bid, 1, "seen_like")
    svc.feedback(bid, 2, "seen")
    rows = training_rows(svc.db)
    assert [r["y"] for r in rows] == [1]          # 读过·喜欢 = 正例；单纯读过不参与
    assert svc.status()["feedback_counts"].get("读过·喜欢") == 1


def test_report_hide_tags_and_manual_honmei(svc):
    from fastapi.testclient import TestClient
    from manga_shelf.importer import import_favorites
    from manga_shelf.server import create_app
    import_favorites(svc.db, svc.adapter("jm"))
    c = TestClient(create_app(svc))
    top = c.get("/api/insights").json()["top_tags"][0]["tag"]
    assert top in c.post("/api/report/hidden", json={"tag": top, "hidden": True}).json()["hidden_tags"]
    d = c.get("/api/insights").json()
    assert top not in [t["tag"] for t in d["top_tags"]] and d["hidden_tags"] == [top]
    c.post("/api/report/hidden", json={"tag": top, "hidden": False})
    fav = svc.db.collection("jm")[0]
    q = fav.authors[0] if fav.authors else fav.title[:4]
    items = c.get("/api/favorites/search", params={"q": q}).json()["items"]
    assert any(i["wid"] == fav.wid for i in items)
    c.post("/api/honmei", json={"site": "jm", "wid": fav.wid, "on": True})
    assert fav.wid in [b["wid"] for b in c.get("/api/insights").json()["honmei"]]
    c.post("/api/honmei", json={"site": "jm", "wid": fav.wid, "on": False})
    assert not c.get("/api/insights").json()["honmei"]


def test_engine_fallback_when_models_unavailable(monkeypatch):
    from manga_shelf import analyzer
    monkeypatch.setattr(analyzer, "available", lambda s=None: {"api": False, "codex": True, "claude": True})
    monkeypatch.setattr(analyzer, "pick_engine", lambda s=None: "codex")
    def codex(prompt):
        raise analyzer.NoModelError("Codex 试过的模型你的账号都用不了。")
    monkeypatch.setattr(analyzer, "_run_codex", codex)
    monkeypatch.setattr(analyzer, "_run_claude", lambda p: "ok")
    assert analyzer.run("hi") == ("ok", "claude")


def test_portrait_inbox(svc):
    import json
    from manga_shelf.config import data_dir
    from manga_shelf.importer import import_favorites
    from manga_shelf.portrait import import_inbox
    import_favorites(svc.db, svc.adapter("jm"))
    box = data_dir() / "portrait_inbox"
    box.mkdir(exist_ok=True)
    (box / "a.json").write_text(json.dumps({"engine_name": "测试", "portrait": {"title": "测试称号", "one_liner": "x",
        "keywords": [], "serious": [], "bold": []}}, ensure_ascii=False), "utf-8")
    assert import_inbox(svc.db) == 1
    p = svc.db.kv_get("portraits")[0]
    assert p["engine_name"] == "测试" and p["data"]["title"] == "测试称号"
    assert (box / "a.imported").exists() and import_inbox(svc.db) == 0


def test_modes_and_reset(svc):
    from manga_shelf.importer import import_favorites
    from manga_shelf.recommend import MODES, generate_batch
    import_favorites(svc.db, svc.adapter("pica"))
    import_favorites(svc.db, svc.adapter("jm"))
    b1 = generate_batch(svc.db, svc.adapter("jm"), mode="fresh")
    meta = svc.batch(b1)["meta"]
    assert meta["mode"] == "fresh" and meta["mode_name"] == MODES["fresh"]["name"]
    # 尝鲜：每个作者最多一本
    from collections import Counter
    c = Counter(it["authors"][0] for it in svc.batch(b1)["items"] if it["authors"])
    assert max(c.values()) == 1
    b2 = generate_batch(svc.db, svc.adapter("jm"), mode="niche")
    svc.feedback(b2, 1, "want")
    assert [b["vol"] for b in svc.db.list_batches(30)] == [2, 1]
    svc.reset_batches()
    assert svc.db.list_batches(30) == [] and svc.batch(b2) is None
    assert svc.status()["feedback_counts"].get("心动") == 1   # 表态都还在
    b3 = generate_batch(svc.db, svc.adapter("jm"))
    assert svc.batch(b3)["vol"] == 1


def test_pica_pin_and_jm_web_domain():
    import socket
    from manga_shelf import net
    net.pin_host("pica.invalid", "127.0.0.1")
    assert socket.getaddrinfo("pica.invalid", 443)[0][4][0] == "127.0.0.1"
    net.pin_host("pica.invalid", None)
    s = {"jm": {"web_domain": net.JM_WEB_DEFAULT}, "net": {"jm_web": "mirror.example"}}
    assert net.jm_web_domain(s) == "mirror.example"
    s["jm"]["web_domain"] = "mine.example"          # 手动填的优先
    assert net.jm_web_domain(s) == "mine.example"


def test_title_hints_and_themes():
    import random

    from manga_shelf.models import Work
    from manga_shelf.normalize import title_tags
    from manga_shelf.recommend import _pick
    from manga_shelf.themes import Themes

    assert "催眠" in title_tags("[某社团] 催眠アプリで生意気な後輩を")
    assert "女仆" in title_tags("我家的女仆小姐")
    assert not title_tags("[作者] 平凡的日常")

    rnd = random.Random(3)
    pools = [["纯爱", "校服", "青梅竹马"], ["ntr", "人妻", "熟女"], ["触手", "怪物", "异种奸"], ["姐姐", "后宫", "巨乳"]]
    works = []
    for i in range(120):
        tags = rnd.sample(pools[i % 4], 2) + ["长篇" if i % 2 else "短篇"] + rnd.sample(["中出", "口交", "黑丝"], 1)
        works.append(Work(site="jm" if i % 3 else "pica", wid=str(i), title=f"t{i}", tags=tags, detail_ok=True))
    a, b = Themes(works), Themes(works)
    assert [c["tags"] for c in a.clusters] == [c["tags"] for c in b.clusters]          # 结果确定
    assert len(a.clusters) >= 3
    assert all(t not in ("长篇", "短篇") for c in a.clusters for t in c["tags"])        # 格式标签不分味道
    mix = a.mixture(works[:5])
    assert np.allclose(mix.sum(axis=1), 1.0)

    # MMR：分数一样时，div 越大，一卷越不重样
    cands = works[:40]
    scores = [1.0 - i * 0.001 for i in range(40)]
    from manga_shelf.recommend import _similarity
    sim = _similarity(cands)

    def variety(div):
        pick = _pick(cands, scores, ["x"] * 40, 8, 0, 9, random.Random(0), div=div)
        return 1 - np.mean([sim[x, y] for x in pick for y in pick if x < y])
    assert variety(0.6) > variety(0.0)


def test_block_tags(svc):
    from collections import Counter

    from manga_shelf.importer import import_favorites
    from manga_shelf.normalize import norm_tag
    from manga_shelf.recommend import generate_batch
    import_favorites(svc.db, svc.adapter("pica"))
    import_favorites(svc.db, svc.adapter("jm"))
    b1 = generate_batch(svc.db, svc.adapter("jm"), seed=1)
    items = svc.batch(b1)["items"]
    assert len(items) == 12
    top = Counter(t for it in items for t in it["tags"]).most_common(1)[0][0]
    assert svc.block_tag(top) == [top]
    assert any(it["blocked"] for it in svc.batch(b1)["items"])          # 已经出来的这卷标上“已拉黑”
    b2 = generate_batch(svc.db, svc.adapter("jm"), seed=2)
    for it in svc.batch(b2)["items"]:
        assert norm_tag(top) not in {norm_tag(t) for t in svc.db.get_work("jm", it["wid"]).tags}
    assert svc.block_tag(top, False) == []
