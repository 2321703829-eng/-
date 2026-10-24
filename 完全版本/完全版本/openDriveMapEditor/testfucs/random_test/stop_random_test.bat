@echo off
setlocal

set "PORT=18765"
set "SERVICE_PID="

for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr /R /C:":%PORT% .*LISTENING"') do set "SERVICE_PID=%%P"

if not defined SERVICE_PID (
    echo Random-test service is not running on port %PORT%.
    exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/api/config' -TimeoutSec 3; $server = [string]$response.Headers['Server']; if (-not $server.StartsWith('OpenDriveRandomTest/1.0')) { exit 2 } } catch { exit 2 }"
if errorlevel 1 (
    echo ERROR: Port %PORT% does not belong to the OpenDRIVE random-test service.
    echo No process was stopped.
    exit /b 2
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { Stop-Process -Id %SERVICE_PID% -Force -ErrorAction Stop } catch { Write-Error $_.Exception.Message; exit 1 }"
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" echo Stopped random-test service PID %SERVICE_PID% on port %PORT%.

endlocal & exit /b %EXIT_CODE%
