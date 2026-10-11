@echo off
setlocal
title AquaPulse
cd /d "%~dp0"

echo.
echo  ==========================================
echo    AquaPulse - Water Safety Command Center
echo  ==========================================
echo.

:: 1) Find Python 3
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3 was not found on this computer.
  echo         Install it from https://www.python.org/downloads/
  echo         and tick "Add python.exe to PATH" during setup, then run this file again.
  echo.
  pause
  exit /b 1
)

:: 2) Create the virtual environment on first run
if not exist ".venv\Scripts\python.exe" (
  echo [1/3] Creating virtual environment, first run only...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    pause
    exit /b 1
  )
)

:: 3) Install / update dependencies
echo [2/3] Checking dependencies...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Installing dependencies failed. Check your internet connection and try again.
  pause
  exit /b 1
)

if not exist ".env" (
  echo [WARN] No .env file found. The live map and AI assistant will not work until you add your keys.
)

:: 4) Open the browser shortly after the server starts, then run the server
echo [3/3] Starting AquaPulse at http://localhost:8000
echo       Keep this window open. Close it to stop the server.
echo.
start "" /min cmd /c "timeout /t 4 /nobreak >nul & start "" http://localhost:8000"

".venv\Scripts\python.exe" app.py

echo.
echo AquaPulse has stopped.
pause
endlocal
