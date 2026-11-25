# AGENTS.md

本命 HONMEI：本地漫画收藏推荐书架（Python，FastAPI + SQLite）。入口 `python -m manga_shelf serve`，测试 `pytest -q`。

- 用户想要“口味画像 / 侧写”：按 [PORTRAIT.md](PORTRAIT.md) 做（导出提示词 → 写 JSON → `portrait-import`）。
- 用户数据在 `data/shelf.db`；不要读或输出 `secrets` 表里的账号凭据。
