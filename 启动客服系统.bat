@echo off
chcp 65001 > nul
title 智能客服业务系统
cd /d "%~dp0"
set PYTHONPATH=.
echo ========================================================
echo   正在启动 智能客服知识库与业务会话系统...
echo ========================================================
echo.
if exist "D:\python解释器\python.exe" (
    "D:\python解释器\python.exe" run.py
) else (
    python run.py
)
if errorlevel 1 pause