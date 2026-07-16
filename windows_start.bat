@echo off
REM Patchwerk launcher for Windows - double-click to run.
REM   windows_start.bat            -> pad_space patch
REM   windows_start.bat demo       -> named patch
REM First run: creates the Python env and installs dependencies (needs internet once).
REM Everything runs on 127.0.0.1 (loopback) - nothing is exposed to the network.

setlocal
cd /d "%~dp0"
title Patchwerk

REM --- SuperCollider check ---------------------------------------------------
REM supriya finds scsynth via: SUPRIYA_SERVER_EXECUTABLE, then PATH, then
REM "C:\Program Files\SuperCollider*\scsynth.exe". Probe the same places so a
REM missing install fails HERE with a useful message instead of a traceback.
set "SC_FOUND="
if defined SUPRIYA_SERVER_EXECUTABLE if exist "%SUPRIYA_SERVER_EXECUTABLE%" set "SC_FOUND=1"
if not defined SC_FOUND (
  where scsynth >nul 2>nul && set "SC_FOUND=1"
)
if not defined SC_FOUND (
  for /d %%D in ("%ProgramFiles%\SuperCollider*") do (
    if exist "%%D\scsynth.exe" set "SC_FOUND=1"
  )
)
if not defined SC_FOUND (
  echo.
  echo SuperCollider is not installed ^(scsynth.exe not found^).
  echo Download the Windows installer here and re-run this launcher:
  echo   https://supercollider.github.io/downloads
  echo.
  pause
  exit /b 1
)

REM scsynth refuses to boot if its synthdef folder is missing (harmless if it exists)
mkdir "%LOCALAPPDATA%\SuperCollider\synthdefs" 2>nul

REM --- Python environment (bootstraps itself on first run) --------------------
if not exist ".venv\Scripts\python.exe" (
  echo First run: creating the Python environment...
  py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || py -3 -m venv .venv 2>nul || python -m venv .venv
  if not exist ".venv\Scripts\python.exe" (
    echo.
    echo Python 3.10+ not found. Install it from https://www.python.org/downloads/
    echo ^(3.12 recommended; tick "Add python.exe to PATH" in the installer^)
    echo.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Dependency install failed - see the messages above.
    echo ^(If python-rtmidi failed to build, install Python 3.12 and delete the .venv folder.^)
    echo.
    pause
    exit /b 1
  )
)

REM --- stop any stale engine, then launch -------------------------------------
taskkill /f /im scsynth.exe >nul 2>nul

set "PATCH=%~1"
if "%PATCH%"=="" set "PATCH=pad_space"

echo Starting Patchwerk - the browser opens at http://127.0.0.1:8765
echo Keep this window open; hot-reload and error messages appear here.
".venv\Scripts\python.exe" -u -m synthbase gui %PATCH% %2 %3 %4

echo.
echo Patchwerk stopped.
pause
