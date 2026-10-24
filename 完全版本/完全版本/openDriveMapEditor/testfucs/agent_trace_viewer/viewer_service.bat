@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "PROJECT_ROOT=%%~fI"
set "VIEWER_PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
set "VIEWER_SERVER=%SCRIPT_DIR%server.py"
if not defined VIEWER_PORT set "VIEWER_PORT=8730"
set "VIEWER_URL=http://127.0.0.1:%VIEWER_PORT%/"

if "%~1"=="" goto menu
if /I "%~1"=="start" goto command_start
if /I "%~1"=="stop" goto command_stop
if /I "%~1"=="restart" goto command_restart
if /I "%~1"=="status" goto command_status

echo Usage: %~nx0 [start^|stop^|restart^|status]
exit /b 2

:menu
echo.
echo Agent Trace Viewer Service
echo   1. Start
echo   2. Stop
echo   3. Restart
echo   4. Status
echo   0. Exit
echo.
set /p "VIEWER_ACTION=Select: "
if "%VIEWER_ACTION%"=="1" call :start_service
if "%VIEWER_ACTION%"=="2" call :stop_service
if "%VIEWER_ACTION%"=="3" call :restart_service
if "%VIEWER_ACTION%"=="4" call :status_service
if "%VIEWER_ACTION%"=="0" exit /b 0
echo.
pause
exit /b 0

:command_start
call :start_service
exit /b %errorlevel%

:command_stop
call :stop_service
exit /b %errorlevel%

:command_restart
call :restart_service
exit /b %errorlevel%

:command_status
call :status_service
exit /b %errorlevel%

:start_service
if not exist "%VIEWER_PYTHON%" (
  echo ERROR: Python runtime not found: %VIEWER_PYTHON%
  exit /b 1
)
if not exist "%VIEWER_SERVER%" (
  echo ERROR: Viewer server not found: %VIEWER_SERVER%
  exit /b 1
)

curl.exe --noproxy "*" -fsS "%VIEWER_URL%api/health" >nul 2>&1
if not errorlevel 1 (
  echo Viewer service is already running at %VIEWER_URL%
  exit /b 0
)

start "Agent Trace Viewer" /min "%VIEWER_PYTHON%" "%VIEWER_SERVER%" --port "%VIEWER_PORT%"
for /L %%I in (1,1,10) do (
  curl.exe --noproxy "*" -fsS "%VIEWER_URL%api/health" >nul 2>&1
  if not errorlevel 1 goto service_started
  ping.exe 127.0.0.1 -n 2 >nul
)
echo ERROR: Viewer service did not become ready.
exit /b 1

:service_started
echo Viewer service started.
echo URL: %VIEWER_URL%
exit /b 0

:stop_service
curl.exe --noproxy "*" -fsS -X POST -H "X-Viewer-Control: viewer-service-bat" "%VIEWER_URL%api/shutdown" >nul 2>&1
if errorlevel 1 (
  echo Viewer service is already stopped or unreachable.
  exit /b 0
)

for /L %%I in (1,1,10) do (
  curl.exe --noproxy "*" -fsS "%VIEWER_URL%api/health" >nul 2>&1
  if errorlevel 1 goto service_stopped
  ping.exe 127.0.0.1 -n 2 >nul
)
echo ERROR: Viewer service did not stop in time.
exit /b 1

:service_stopped
echo Viewer service stopped.
exit /b 0

:restart_service
call :stop_service
if errorlevel 1 exit /b 1
call :start_service
exit /b %errorlevel%

:status_service
curl.exe --noproxy "*" -fsS "%VIEWER_URL%api/health" >nul 2>&1
if errorlevel 1 (
  echo Viewer service is stopped or unreachable at %VIEWER_URL%
  exit /b 1
)
echo Viewer service is running.
echo URL: %VIEWER_URL%
exit /b 0
