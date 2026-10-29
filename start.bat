@echo off
chcp 65001 >nul
title 智语云 · AI 智能客服 SaaS 平台
cd /d "%~dp0"
set PYTHONPATH=.

echo ========================================================================
echo   🤖 智语云 · AI 智能客服 SaaS 平台 - 一键启动
echo ========================================================================
echo   🌐 官网与订阅入口: http://localhost:8000/
echo   📖 API 接口文档:   http://localhost:8000/docs
echo ------------------------------------------------------------------------
echo   【演示账号】(除平台超管外均归属同一租户与产品线，支持全链路测试):
echo    👑 平台超级管理员: platform_admin / admin123
echo    🏢 租户超级管理员: admin_tenant   / admin123
echo    📚 知识库专家主管: km_001         / admin123
echo    🎧 一线人工客服:   agent_1028     / admin123
echo    🙋 终端普通客户:   user_001       / 123456
echo ========================================================================
echo   🚀 正在检查 Python 环境并启动服务...
echo ========================================================================
echo.

set "PY_CMD="

if exist "D:\python解释器\python.exe" (
    set "PY_CMD=D:\python解释器\python.exe"
    goto :RUN
)

if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0.venv\Scripts\python.exe"
    goto :RUN
)

if exist "%~dp0venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0venv\Scripts\python.exe"
    goto :RUN
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :RUN
)

echo [错误] 未检测到可用的 Python 运行环境！
echo 请确保 Python 已正确安装并配置至环境变量 PATH，或安装在 "D:\python解释器\python.exe"。
echo.
pause
exit /b 1

:RUN
echo [信息] 正在使用 Python 解释器: %PY_CMD%
"%PY_CMD%" run.py

if errorlevel 1 (
    echo.
    echo [提示] 服务已停止或发生异常。
    pause
)
