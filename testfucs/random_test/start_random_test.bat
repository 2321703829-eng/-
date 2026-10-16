@echo off
setlocal

set "PORT=18765"
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "PROJECT_ROOT=%%~fI"
set "PYTHON_EXE=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "SERVICE_SCRIPT=%SCRIPT_DIR%batch_random_modify.py"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python environment not found: %PYTHON_EXE%
    exit /b 1
)

if not exist "%SERVICE_SCRIPT%" (
    echo ERROR: Service script not found: %SERVICE_SCRIPT%
    exit /b 1
)

for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:":%PORT% .*LISTENING"') do (
    echo ERROR: Port %PORT% is already in use by PID %%P.
    echo Run stop_random_test.bat if this is the random-test service.
    exit /b 1
)

echo Starting OpenDRIVE random-test service on http://127.0.0.1:%PORT%/
start "OpenDRIVE Random Test" /D "%PROJECT_ROOT%" "%PYTHON_EXE%" "%SERVICE_SCRIPT%" --web --port %PORT%
if errorlevel 1 (
    echo ERROR: Failed to launch the random-test service.
    exit /b 1
)

echo Service launch requested. The web page will open automatically.
exit /b 0
