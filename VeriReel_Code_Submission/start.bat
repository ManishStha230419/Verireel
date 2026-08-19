@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\waitress-serve.exe" (
    echo [INFO] First run detected. Installing VeriReel dependencies...
    call "%~dp0setup.bat" --no-pause
    if errorlevel 1 goto :setup_failed
)

set "VERIREEL_PORT_PID="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:"127.0.0.1:5000 .*LISTENING"') do set "VERIREEL_PORT_PID=%%P"

if defined VERIREEL_PORT_PID (
    echo [INFO] VeriReel is already running at http://127.0.0.1:5000
    echo [INFO] A second copy was not started.
    if not defined VERIREEL_NO_BROWSER start "" "http://127.0.0.1:5000"
    pause
    exit /b 0
)

echo ============================================================
echo  VeriReel is starting at http://127.0.0.1:5000
echo  Keep this window open. Press Ctrl+C to stop the site.
echo ============================================================
echo.
if not defined VERIREEL_NO_BROWSER start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command "$url='http://127.0.0.1:5000'; for($i=0; $i -lt 60; $i++){ try { $response=Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2; if($response.StatusCode -eq 200){ Start-Process $url; break } } catch {}; Start-Sleep -Seconds 1 }"
".venv\Scripts\waitress-serve.exe" --listen=127.0.0.1:5000 app:app

if errorlevel 1 (
    echo.
    echo [ERROR] VeriReel could not start. Check whether port 5000 is in use.
    pause
    exit /b 1
)

exit /b 0

:setup_failed
echo.
echo [ERROR] VeriReel could not be prepared. Fix the setup error above and try again.
pause
exit /b 1
