@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop';" ^
  "try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 2 } catch { Write-Host 'ResultAna is not running.'; exit 0 };" ^
  "if ($health.application -ne 'ResultAna') { Write-Host 'Port 8765 is not ResultAna. Nothing was stopped.'; exit 2 };" ^
  "$listener = netstat -ano | Select-String '^\s*TCP\s+127\.0\.0\.1:8765\s+\S+\s+LISTENING\s+(\d+)\s*$' | Select-Object -First 1;" ^
  "if (-not $listener) { Write-Host 'ResultAna responded, but its listener process was not found.'; exit 3 };" ^
  "$serverPid = [int]$listener.Matches[0].Groups[1].Value;" ^
  "Stop-Process -Id $serverPid -ErrorAction Stop;" ^
  "Write-Host ('ResultAna stopped. PID: ' + $serverPid);"

set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Failed to stop ResultAna.
  pause
)
exit /b %EXIT_CODE%
