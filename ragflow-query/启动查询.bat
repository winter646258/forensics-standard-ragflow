@echo off
chcp 65001 >nul
title 电子取证标准查询 - 本地服务
cd /d "%~dp0"

echo.
echo   ==================================================
echo      电子取证标准查询  ·  本地服务
echo   ==================================================
echo.

rem ---------- 1. 找 Python ----------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py" )
if not defined PY (
    echo   [错误] 未找到 Python。
    echo          请安装 Python 3.8 或更高版本，安装时勾选
    echo          "Add Python to PATH"，然后重新双击本文件。
    echo.
    pause
    exit /b 1
)

rem ---------- 2. 检查知识库 ----------
echo   [1/2] 检查知识库服务 ...
set "HAS_CURL="
where curl >nul 2>nul && set "HAS_CURL=1"

if defined HAS_CURL (
    curl -s -o nul --max-time 6 http://127.0.0.1:8081/ 2>nul
    if errorlevel 1 goto :startdocker
)
echo         知识库在线。
goto :runapp

:startdocker
echo         知识库未响应，正在调起 Docker Desktop ...
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" (
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    echo         已发出启动指令，等待 45 秒让容器就绪 ...
    timeout /t 45 /nobreak >nul
) else (
    echo         [提示] 未找到 Docker Desktop。
    echo                请手动启动它并等 RAGFlow 就绪；页面仍会打开，
    echo                但查询会失败，直到知识库恢复。
    timeout /t 6 /nobreak >nul
)

:runapp
rem ---------- 3. 启动应用 ----------
echo   [2/2] 启动查询页面 ...
echo.
%PY% app.py --open

echo.
echo   服务已停止。按任意键关闭窗口。
pause >nul
