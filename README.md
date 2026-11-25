# 本命 HONMEI

> 替你找下一本本命。本地优先的个人漫画推荐书架：导入哔咔 / 禁漫收藏，在禁漫里推荐还没收藏的作品。所有数据只在本机。

这份 README 主要写给帮用户安装、使用这个项目的 AI 助手（Codex、Claude Code 等）。

## 安装与启动（Windows）

- 需要 Python 3.10+。用户双击 `start-shelf.bat` 即可：自动建 `.venv`、装依赖、打开 `http://127.0.0.1:8765`。
- 手动：`python -m venv .venv && .venv\Scripts\pip install -e .`，然后 `.venv\Scripts\python -m manga_shelf serve`。
- 只看界面：`python -m manga_shelf demo --open`（假数据、独立数据目录）。
- 可选语义匹配：`install-semantic.bat`；桌面图标：`make-shortcut.bat`。

用户在网页“设置”里登录两站、导入收藏，然后点“开卷 ✦”。

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

- `manga_shelf/sources/`：站点适配器（`jm.py`、`pica.py`、演示 `demo.py`）
- `normalize.py` 标题拆解、标签归一、版本去重 · `taste.py` 口味模型（提升度、口味簇、基准样本、跨站标签对照）
- `features.py` 候选特征 · `ranker.py` 个人排序 · `recommend.py` 检索与组卷 · `calibrate.py` 调音 · `evaluate.py` 检验
- `insights.py` / `portrait.py` 本命报告与侧写 · `analyzer.py` 可选 AI 解读（Codex / Claude Code / OpenAI 兼容 API）
- `server.py` FastAPI · `static/` 前端（`index.html` 书架，`report.html` 报告）

开发：`pip install -e ".[dev]" && pytest -q`。待办见 `TODO.md`。
