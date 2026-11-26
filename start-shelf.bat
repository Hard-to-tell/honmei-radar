@echo off
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run

echo 第一次运行：正在创建本地环境并安装依赖，大约需要几分钟……
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY python --version >nul 2>nul && set "PY=python"
if not defined PY (
  echo 没有找到 Python，正在自动安装（需要一两分钟）……
  winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements >nul 2>nul
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe""
)
if not defined PY goto nopython

%PY% -m venv .venv
if errorlevel 1 goto nopython
".venv\Scripts\python.exe" -m pip install -U pip >nul
".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
  echo 默认源安装失败，改用清华镜像重试……
  ".venv\Scripts\python.exe" -m pip install -e . -i https://pypi.tuna.tsinghua.edu.cn/simple
  if errorlevel 1 goto installfail
)

:run
".venv\Scripts\python.exe" -m manga_shelf serve --open
pause
exit /b 0

:nopython
echo 没有找到 Python。请先安装 Python 3.10 或更高版本：https://www.python.org/downloads/
echo 安装时勾选 "Add python.exe to PATH"，装好后再双击本文件。
pause
exit /b 1

:installfail
echo 依赖安装失败。可以检查网络或代理后再试；删除 .venv 文件夹可以从头重装。
pause
exit /b 1
