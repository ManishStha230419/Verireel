@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  VeriReel - fresh-start cleanup
echo ============================================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset.ps1"
set "RESET_EXIT=%ERRORLEVEL%"

echo.
if not "%RESET_EXIT%"=="0" (
    echo [ERROR] Cleanup did not finish. Close any program using this folder and try again.
) else (
    echo Double-click start.bat whenever you want a brand-new installation.
)

if /I not "%~1"=="--no-pause" pause
exit /b %RESET_EXIT%
