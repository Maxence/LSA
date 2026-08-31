@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 main_assist.py
) else (
    python main_assist.py
)
if errorlevel 1 pause
