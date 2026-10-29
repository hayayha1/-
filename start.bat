@echo off
chcp 65001 >nul
title 智语云 · AI 智能客服 SaaS 平台
cd /d "%~dp0"
set PYTHONPATH=.

echo ========================================================================
echo   🤖 智语云 · AI 智能客服 SaaS 平台 - 一键启动
echo ========================================================================
echo   🌐 产品官网 & 订阅中心: http://localhost:8000/
echo   📖 API 接口交互文档:   http://localhost:8000/docs
echo ------------------------------------------------------------------------
echo   【各角色快捷演示账号】(均已连通真实鉴权与业务闭环):
echo    👑 SaaS平台超级管理员: platform_admin / admin123  (租户大盘与运营报表)
echo    🏢 企业租户超级管理员: admin_tenant   / admin123  (产品线、知识库与坐席)
echo    📚 知识库专家主管:     km_001         / admin123  (知识架构与文档同步)
echo    🎧 一线技术客服坐席:   agent_1028     / admin123  (接单协同与排错回复)
echo    🙋 终端普通客户账号:   user_001       / 123456    (沉浸单聊与转人工)
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
echo [信息] 正在启动智能客服系统服务，稍后将自动为您打开浏览器...
echo.

"%PY_CMD%" run.py

if errorlevel 1 (
    echo.
    echo [提示] 服务已停止或发生异常。按任意键退出窗口。
    pause >nul
)
