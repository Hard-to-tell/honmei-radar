# AGENTS.md

本命 HONMEI：本地漫画收藏推荐书架（Python，FastAPI + SQLite）。用户多半是零基础，替他把事情做完，少让他动手。

## 帮用户安装并打开（Windows）

1. 没有 Python 3.10+ 时：`winget install -e --id Python.Python.3.12 --scope user`（失败就请用户从 python.org 装，勾选 Add to PATH）。
2. 在项目目录运行 `start-shelf.bat`：会建 `.venv`、装依赖、打开 `http://127.0.0.1:8765`。
   手动等价：`python -m venv .venv && .venv\Scripts\pip install -e .`，然后 `.venv\Scripts\python -m manga_shelf serve --open`。
3. 告诉用户：在网页里登录哔咔 / 禁漫 → 导入收藏 → 点“开卷 ✦”。以后双击 `start-shelf.bat` 即可；`make-shortcut.bat` 建桌面图标。

- 只看界面：`python -m manga_shelf demo --open`（假数据、独立数据目录）。
- 可选语义匹配：`install-semantic.bat`。
- 网络：书架会自己试直连、备用线路和本机常见代理端口，一般不用手动配。

## 命令

| 命令 | 作用 |
|---|---|
| `python -m manga_shelf serve` | 启动书架网页 |
| `python -m manga_shelf eval` | 离线检验推荐算法（需联网） |
| `python -m manga_shelf prompt -o portrait_prompt.txt` | 导出“口味侧写”提示词 |
| `python -m manga_shelf portrait-import FILE --name 名字` | 把写好的侧写存进本命报告 |
| `python -m manga_shelf mcp` | 本地 MCP 服务（generate_batch / get_batch / explain_item / record_feedback / open_shelf） |

用户想要“口味画像 / 侧写”时，按 [PORTRAIT.md](PORTRAIT.md) 做。

## 规则

- 用户数据在 `data/`（`shelf.db`、`settings.json`、封面缓存、日志），已被 `.gitignore` 排除。
- `shelf.db` 的 `secrets` 表存有账号凭据：**不要读取、输出或发送给任何模型或外部服务**。需要拿数据库做分析时，先复制一份并 `DELETE FROM secrets`。
- 不要把用户的收藏标题、作者等个人数据提交到仓库或发到网上。

## 代码地图

- `manga_shelf/sources/`：站点适配器（`jm.py`、`pica.py`、演示 `demo.py`）· `net.py` 网络自动检测（代理、备用线路）
- `normalize.py` 标题拆解、标签归一、版本去重 · `taste.py` 口味模型（提升度、基准样本、跨站标签对照）· `themes.py` 口味主题（NMF 软分组）
- `features.py` 候选特征 · `ranker.py` 个人排序 · `recommend.py` 检索与组卷（开卷口味模式） · `calibrate.py` 调音 · `evaluate.py` 检验
- `insights.py` / `portrait.py` 本命报告与侧写 · `analyzer.py` 可选 AI 解读（Codex / Claude Code / OpenAI 兼容 API）
- `server.py` FastAPI · `static/` 前端（`index.html` 书架，`report.html` 报告）

开发：`pip install -e ".[dev]" && pytest -q`。待办见 `TODO.md`。
