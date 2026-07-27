@echo off
REM Patchwerk (Windows) - launches with no console window.
REM
REM The Start Menu shortcut points straight at pythonw.exe; this file exists
REM so that running Patchwerk from the install folder does the same thing.
REM
REM Descendant of windows_start.bat (archive/artifix-patches @ 462589d), but
REM far thinner, and deliberately so: that script had to find a Python, build
REM a venv, pip-install, probe for SuperCollider and clear stale servers,
REM because it ran against a bare checkout. The installer has already put a
REM complete Python and every dependency in this folder, and launcher.py owns
REM SuperCollider discovery and scsynth hygiene on both platforms. What is
REM left is: start it.
REM
REM Everything runs on 127.0.0.1 - nothing is exposed to the network.

setlocal
cd /d "%~dp0"
start "" "%~dp0python\pythonw.exe" "%~dp0launcher.py" %*
