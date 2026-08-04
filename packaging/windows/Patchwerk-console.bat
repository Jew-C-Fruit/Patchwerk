@echo off
REM Patchwerk (Windows) - diagnostic launch, WITH a console window.
REM
REM Same app, but console-attached so startup messages, hot-reload output and
REM tracebacks are visible. This is the one to run when something is wrong,
REM and the one to screenshot when reporting it.
REM
REM The engine's own log is written to %USERPROFILE%\.patchwerk\patchwerk.log
REM regardless of which launcher you use.

setlocal
cd /d "%~dp0"
title Patchwerk (console)

echo Starting Patchwerk. The browser opens on its own.
echo Keep this window open - closing it stops Patchwerk.
echo.

"%~dp0python\python.exe" -u "%~dp0launcher.py" %*

echo.
echo Patchwerk stopped.
pause
