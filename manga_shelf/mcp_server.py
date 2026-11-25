"""本地 MCP 服务（stdio）。Codex、Claude Code 等支持 MCP 的客户端都能接入。

工具和网页共用同一个数据库和同一份推荐证据，所以两边对同一张卡的解释一致。
"""
from __future__ import annotations

import webbrowser

from .service import LABELS, Service

PORT = 8765


def _server_cls():
    try:  # mcp 2.x
        from mcp.server.mcpserver import MCPServer
        return MCPServer
    except ImportError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP
        return FastMCP


def build(svc: Service | None = None):
    svc = svc or Service()
    server = _server_cls()(name="honmei", instructions=(
        "本命 HONMEI：本地漫画推荐书架。generate_batch 生成一批禁漫推荐；get_batch 查看某批（默认最新）；"
        "explain_item 解释某批第几本为什么推荐；record_feedback 记录心动(want)/略过(skip)/不喜欢(dislike)/读过(seen)；"
        "open_shelf 在浏览器打开书架页。解释时只依据返回的证据，不要编造画风等信息。"
    ))

    def brief(b: dict | None) -> dict:
        if not b:
            return {"message": "还没有推荐批次，先调用 generate_batch"}
        return {
            "batch_id": b["id"], "engine": b["engine"],
            "ranker": b["meta"].get("ranker_message", ""),
            "items": [
                {"pos": it["pos"], "title": it["title"], "authors": it["authors"], "tags": it["tags"][:8],
                 "reason": it["reason"].get("text", ""), "url": it["url"],
                 "feedback": LABELS.get(it["feedback"]) if it["feedback"] else None}
                for it in b["items"]
            ],
            "shelf_url": f"http://127.0.0.1:{PORT}/#batch={b['id']}",
        }

    @server.tool()
    def generate_batch() -> dict:
        """生成一批新的禁漫推荐（需要先在书架网页里登录并导入收藏），返回整批卡片。"""
        bid = svc.generate_now()
        return brief(svc.batch(bid))

    @server.tool()
    def get_batch(batch_id: int | None = None) -> dict:
        """查看某一批推荐，不传 batch_id 则返回最新一批。"""
        return brief(svc.batch(batch_id))

    @server.tool()
    def explain_item(batch_id: int, pos: int) -> dict:
        """解释某批第 pos 本（从 1 开始）为什么推荐：返回当时保存的匹配证据和本地说明。"""
        return svc.explain(batch_id, pos, deep=False)

    @server.tool()
    def record_feedback(batch_id: int, pos: int, label: str) -> dict:
        """记录反馈。label 取 want（心动）、skip（略过）、dislike（不喜欢）、seen（读过）或 none（撤销）。"""
        return svc.feedback(batch_id, pos, None if label in ("none", "", "undo") else label)

    @server.tool()
    def open_shelf(batch_id: int | None = None) -> str:
        """在浏览器打开书架页（需要书架服务已在运行）。"""
        url = f"http://127.0.0.1:{PORT}/" + (f"#batch={batch_id}" if batch_id else "")
        webbrowser.open(url)
        return url

    return server


def main() -> None:
    build().run("stdio")
