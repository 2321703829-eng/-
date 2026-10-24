@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=%~dp0..\openDriveMapEditor\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"

"%PYTHON%" -B -m visualization.server
if errorlevel 1 (
  echo.
  echo ResultAna failed to start. Check that Python and requirements are installed.
  pause
)
