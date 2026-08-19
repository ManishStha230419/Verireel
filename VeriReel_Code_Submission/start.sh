#!/usr/bin/env bash
set -u

cd -- "$(dirname -- "$0")"

APP_URL="http://127.0.0.1:5000"
PYTHON_BIN=""
PYTHON_OK=0

if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
fi

if [ -n "$PYTHON_BIN" ] && "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PYTHON_OK=1
fi

if [ ! -x ".venv/bin/waitress-serve" ] || [ ! -f ".env" ]; then
    echo "============================================================"
    echo " VeriReel - first-time setup"
    echo "============================================================"
    echo "[INFO] Creating an isolated Python environment..."
    if [ "$PYTHON_OK" -eq 1 ]; then
        if ! "$PYTHON_BIN" -m venv .venv; then
            echo "[INFO] The system Python cannot create an environment; using a managed Python instead."
            PYTHON_OK=0
        fi
    fi

    if [ "$PYTHON_OK" -ne 1 ]; then
        echo "[INFO] Compatible Python was not found."
        echo "[INFO] Downloading the portable setup helper from its official source..."
        mkdir -p .bootstrap
        if command -v curl >/dev/null 2>&1; then
            if ! curl -LsSf https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$PWD/.bootstrap" sh; then
                echo "[ERROR] The setup helper could not be downloaded."
                exit 1
            fi
        elif command -v wget >/dev/null 2>&1; then
            if ! wget -qO- https://astral.sh/uv/install.sh | env UV_UNMANAGED_INSTALL="$PWD/.bootstrap" sh; then
                echo "[ERROR] The setup helper could not be downloaded."
                exit 1
            fi
        else
            echo "[ERROR] Either curl or wget is required for the automatic Python download."
            exit 1
        fi

        echo "[INFO] Downloading managed Python 3.12 and creating the environment..."
        if ! .bootstrap/uv venv --python 3.12 --seed .venv; then
            echo "[ERROR] Python 3.12 could not be downloaded automatically."
            exit 1
        fi
        rm -rf -- "$PWD/.bootstrap"
    fi

    echo "[INFO] Updating the package installer..."
    .venv/bin/python -m pip install --disable-pip-version-check --upgrade pip setuptools wheel || exit 1

    echo "[INFO] Installing the exact dependencies from requirements-lock.txt..."
    .venv/bin/python -m pip install --disable-pip-version-check -r requirements-lock.txt || exit 1

    [ -f .env ] || cp .env.example .env
    mkdir -p temp

    echo "[INFO] Verifying the installed dependencies..."
    .venv/bin/python -c 'import cv2, curl_cffi, dotenv, flask, imagehash, numpy, PIL, pywt, reportlab, scipy, waitress, yt_dlp' || exit 1
    echo "[OK] Setup complete."
fi

if .venv/bin/python -c 'import socket; s=socket.socket(); s.settimeout(0.3); code=s.connect_ex(("127.0.0.1", 5000)); s.close(); raise SystemExit(0 if code == 0 else 1)'; then
    echo "[INFO] VeriReel is already running at $APP_URL"
    if [ "${VERIREEL_NO_BROWSER:-0}" != "1" ]; then
        .venv/bin/python -c "import webbrowser; webbrowser.open('$APP_URL')" >/dev/null 2>&1 || true
    fi
    exit 0
fi

echo "============================================================"
echo " VeriReel is starting at $APP_URL"
echo " Keep this terminal open. Press Ctrl+C to stop the site."
echo "============================================================"

if [ "${VERIREEL_NO_BROWSER:-0}" != "1" ]; then
    .venv/bin/python -c "import time, urllib.request, webbrowser
url='$APP_URL'
for _ in range(60):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if response.status == 200:
                webbrowser.open(url)
                break
    except Exception:
        time.sleep(1)
" >/dev/null 2>&1 &
fi

exec .venv/bin/waitress-serve --listen=127.0.0.1:5000 app:app
