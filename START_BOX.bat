@echo off
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
    py -3 box_assist.py
) else (
    python box_assist.py
)
if errorlevel 1 pause
