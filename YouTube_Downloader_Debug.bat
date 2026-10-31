@echo off
chcp 65001 > nul
set PYTHONUTF8=1
set PATH=%~dp0bin;%PATH%
cd /d "%~dp0"
echo ========================================================
echo   YouTube ^& Spotify Downloader - Debug Console Mode
echo ========================================================
echo [DEBUG] Running Python backend with local embedded Python 3.12:
echo [DEBUG] Path: %~dp0runtime\python.exe
echo.
"%~dp0runtime\python.exe" -m uvicorn src.panel.app:app --host 127.0.0.1 --port 8000
pause
