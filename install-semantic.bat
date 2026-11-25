@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 请先双击 start-shelf.bat 完成首次安装。
  pause
  exit /b 1
)
echo 正在安装语义匹配需要的组件（含 PyTorch CPU 版，约几百 MB）……
".venv\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cpu
".venv\Scripts\python.exe" -m pip install -e ".[semantic]"
if errorlevel 1 ".venv\Scripts\python.exe" -m pip install -e ".[semantic]" -i https://pypi.tuna.tsinghua.edu.cn/simple
echo.
echo 安装完成。到书架的“账号与设置”里勾选“语义匹配”，下一批推荐时会自动下载模型。
pause
