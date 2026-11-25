# 给 AI 桌面端：怎么替用户写“口味画像”

如果你是 Codex / Claude / 其他 AI 桌面端，用户把这个仓库交给你并说“帮我写侧写 / 画像”，照下面做：

1. **准备环境**（Windows 用户通常已经双击过 `start-shelf.bat`，有 `.venv` 了）：
   - 有 `.venv`：用 `.venv\Scripts\python`（Windows）或 `.venv/bin/python`。
   - 没有：`python -m venv .venv`，再 `pip install -e .`。
   - 用户的数据在仓库下的 `data/`（`shelf.db`）。**不要读 `secrets` 表**，那里是账号凭据。
2. **导出提示词**：`python -m manga_shelf prompt -o portrait_prompt.txt`
   这个文件就是完整任务：一段写好的提示词 + 用户的口味摘要（统计、标签、一部分作品标题，调音里的本命一定在内）。
   如果提示说“还没有收藏”，请用户先在书架里导入。
3. **照文件里的要求写画像**：只输出一个 JSON 对象，字段见提示词。你可以比提示词里写得更细，但别丢字段。
4. **存回去**：把 JSON 存成 `portrait.json`，运行
   `python -m manga_shelf portrait-import portrait.json --name "你的名字（比如 Claude 桌面端）"`
   然后告诉用户：打开书架顶栏的“本命报告”，滑到最后就能看到。
   拿不到 Python 环境时，也可以把 JSON 存进 `data/portrait_inbox/`（任意文件名 `.json`），书架下次打开报告时会自动收进去。
5. 也可以直接在对话里把画像念给用户听，但仍然建议存回去，报告页排版更好看。

底线（和提示词里一致）：不做道德评判、不做精神疾病诊断、不写露骨描写；年龄看起来偏小的标签只当画风设定中性带过；
不编造摘要里没有的作品；不读、不外传账号凭据。
