@echo off
:: SENTINEL AI — Windows start script
:: Double-click this file or run from PowerShell/CMD

echo.
echo  ╔══════════════════════════════════════════════════════════╗
echo  ║         SENTINEL AI — Industrial Safety Platform         ║
echo  ║         Multi-Agent Compound Risk Intelligence           ║
echo  ╚══════════════════════════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found.
    echo Install Python 3.11+ from https://python.org/downloads
    echo Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

:: Install dependencies
echo Installing dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: pip install failed. See output above.
    pause
    exit /b 1
)

echo.
echo  Starting SENTINEL AI...
echo.
echo   Dashboard :  http://localhost:8000
echo   API docs  :  http://localhost:8000/docs
echo   AI Setup  :  Click "AI Settings" in the dashboard top bar
echo.
echo  Press Ctrl+C to stop.
echo.

python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload --loop asyncio

pause
