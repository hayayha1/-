@echo off
title AI Customer Service System
cd /d "%~dp0"
set PYTHONPATH=.

if exist "D:\python½âÊÍÆ÷\python.exe" (
    "D:\python½âÊÍÆ÷\python.exe" run.py
) else (
    python run.py
)

if errorlevel 1 pause