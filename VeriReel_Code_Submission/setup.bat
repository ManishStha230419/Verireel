@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo  VeriReel - first-time setup
echo ============================================================
echo.

set "PYTHON_LAUNCHER="

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
    if not errorlevel 1 goto install_dependencies
)

if exist ".venv" (
    echo [INFO] Removing an incomplete or incompatible environment...
    call :remove_stale_venv
    if exist ".venv" goto stale_environment_failed
)

py -3.12 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_LAUNCHER=py -3.12"

if not defined PYTHON_LAUNCHER (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_LAUNCHER=py -3"
)

if not defined PYTHON_LAUNCHER (
    python --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_LAUNCHER=python"
)

if not defined PYTHON_LAUNCHER goto bootstrap_python

%PYTHON_LAUNCHER% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 goto bootstrap_python

echo [INFO] Creating an isolated Python environment...
%PYTHON_LAUNCHER% -m venv ".venv"
if errorlevel 1 goto failed
goto install_dependencies

:bootstrap_python
echo [INFO] Compatible Python was not found.
echo [INFO] Downloading the portable setup helper from its official source...
if not exist ".bootstrap" mkdir ".bootstrap"
if not exist ".bootstrap\uv.exe" (
    set "UV_UNMANAGED_INSTALL=%CD%\.bootstrap"
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "UV_UNMANAGED_INSTALL="
    if errorlevel 1 goto python_download_failed
)
if not exist ".bootstrap\uv.exe" goto python_download_failed

echo [INFO] Downloading managed Python 3.12 and creating the environment...
".bootstrap\uv.exe" venv --python 3.12 --seed ".venv"
if errorlevel 1 goto python_download_failed
if not exist ".venv\Scripts\python.exe" goto python_download_failed
rmdir /s /q ".bootstrap" >nul 2>&1

:install_dependencies

echo [INFO] Updating the package installer...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel
if errorlevel 1 goto failed

echo [INFO] Installing the exact application dependencies from requirements-lock.txt...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "requirements-lock.txt"
if errorlevel 1 goto failed

if not exist ".env" copy ".env.example" ".env" >nul
if not exist "temp" mkdir "temp"

echo [INFO] Verifying the installed dependencies...
".venv\Scripts\python.exe" -c "import cv2, curl_cffi, dotenv, flask, imagehash, numpy, PIL, pywt, reportlab, scipy, waitress, yt_dlp"
if errorlevel 1 goto failed

echo.
echo [OK] Setup complete.
echo.
echo Start the site with:
echo   start.bat
echo.
echo Then open http://127.0.0.1:5000
echo ============================================================
exit /b 0

:remove_stale_venv
rmdir ".venv\lib64" >nul 2>&1
rmdir /s /q ".venv\lib" >nul 2>&1
rmdir /s /q ".venv\bin" >nul 2>&1
rmdir /s /q ".venv" >nul 2>&1
exit /b 0

:stale_environment_failed
echo [ERROR] The old .venv folder is locked and could not be replaced.
echo Close terminals or programs using this folder, then run start.bat again.
goto failed

:python_download_failed
echo [ERROR] Python 3.12 could not be downloaded automatically.
echo Check the internet connection, or install Python manually from:
echo https://www.python.org/downloads/windows/
goto failed

:failed
echo.
echo [ERROR] Setup did not complete. Review the message above.
if /I not "%~1"=="--no-pause" pause
exit /b 1
