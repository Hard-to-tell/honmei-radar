"""命令行入口。

    python -m manga_shelf serve [--open] [--port 8765]   启动书架网页
    python -m manga_shelf mcp                            启动 MCP 服务（给 Codex 等用）
    python -m manga_shelf demo [--open]                  用假数据体验整个流程（独立数据目录）
    python -m manga_shelf eval                           离线评测：新旧推荐算法对比
    python -m manga_shelf prompt [-o 文件]                导出口味画像的完整提示词（含你的口味摘要）
    python -m manga_shelf portrait-import 文件            把在别处写好的画像（JSON）存进本命报告
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser


def _probe(port: int) -> str:
    """看这个端口上是什么：'honmei'（书架已经开着）、'other'（被别的程序占了）、'free'。"""
    import json
    import socket
    import urllib.request
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            pass
    except OSError:
        return "free"
    try:
        op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        d = json.loads(op.open(f"http://127.0.0.1:{port}/api/status", timeout=3).read())
        return "honmei" if "sites" in d else "other"
    except Exception:
        return "other"


def _open_when_ready(url: str, port: int, t0: float) -> None:
    """等服务真的能连上了再开浏览器（慢电脑上固定等 1 秒常常不够）。"""
    import socket
    import time
    deadline = time.time() + 120
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                break
        except OSError:
            time.sleep(0.2)
    import logging
    logging.getLogger("honmei").info("启动用时 %.1fs", time.time() - t0)
    print(f"已就绪（{time.time() - t0:.1f} 秒）")
    webbrowser.open(url)


def _serve(port: int, open_browser: bool) -> None:
    import time
    t0 = time.time()
    print("本命 HONMEI 正在启动……")
    # 已经开着就直接打开页面，不重复启动；端口被别的程序占了就换一个
    for p in range(port, port + 10):
        state = _probe(p)
        if state == "honmei":
            url = f"http://127.0.0.1:{p}/"
            print(f"书架已经开着了：{url}")
            if open_browser:
                webbrowser.open(url)
            sys.exit(3)   # 启动脚本看到 3 就直接关窗口
        if state == "free":
            port = p
            break

    import uvicorn

    from .server import create_app

    app = create_app()
    url = f"http://127.0.0.1:{port}/"
    from .config import data_dir
    print(f"地址：{url}")
    print(f"数据目录：{data_dir()}  （关闭这个窗口即可退出）")
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, port, t0), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(prog="manga_shelf")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true")
    d = sub.add_parser("demo")
    d.add_argument("--port", type=int, default=8766)
    d.add_argument("--open", action="store_true")
    sub.add_parser("mcp")
    sub.add_parser("eval")
    pr = sub.add_parser("prompt")
    pr.add_argument("-o", "--out")
    pi = sub.add_parser("portrait-import")
    pi.add_argument("file")
    pi.add_argument("--name", default="外部 AI")
    args = ap.parse_args(argv)

    if args.cmd == "prompt":
        from .db import DB
        from .portrait import full_prompt
        t = full_prompt(DB())
        if not t:
            print("还没有收藏，先在书架里导入。")
            return
        if args.out:
            open(args.out, "w", encoding="utf-8").write(t)
            print(f"已写入 {args.out}（{len(t)} 字）")
        else:
            print(t)
        return
    if args.cmd == "portrait-import":
        from .db import DB
        from .portrait import save_external
        r = save_external(DB(), open(args.file, encoding="utf-8").read(), args.name)
        print("已存进本命报告：" + ((r["data"] or {}).get("title") or "（没解析成 JSON，原文保存）"))
        return
    if args.cmd == "eval":
        from .evaluate import evaluate
        from .service import Service
        svc = Service()
        r = evaluate(svc.db, svc.adapter("jm"), lambda msg, *a: print(msg))
        print(r["summary"])
        for k, v in r["variants"].items():
            print(f"  {v['name']}: 一卷 {r['top']} 本命中 {v['hits']}，命中平均名次 {v['hit_rank']}，"
                  f"答案平均名次百分位 {v['pos_pct']}，AUC {v['auc']}，哔咔近邻 {v['pica_near']}，覆盖簇 {v['clusters']}")
        return
    if args.cmd == "mcp":
        from .mcp_server import main as mcp_main
        mcp_main()
    elif args.cmd == "demo":
        from pathlib import Path
        home = Path.home() / ".manga-shelf-demo"
        os.environ["MANGA_SHELF_HOME"] = str(home)
        from .config import save_settings
        save_settings({"demo": True})
        _serve(args.port, args.open)
    else:
        _serve(getattr(args, "port", 8765), getattr(args, "open", False))


if __name__ == "__main__":
    main()
