#!/usr/bin/env bash
set -u

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)" || exit 1
cd -- "$PROJECT_DIR" || exit 1

echo "============================================================"
echo " VeriReel - fresh-start cleanup"
echo "============================================================"
echo
echo "[INFO] Stopping VeriReel processes from this folder..."

STOPPED_PIDS=""
for process_dir in /proc/[0-9]*; do
    [ -d "$process_dir" ] || continue
    process_id="${process_dir##*/}"
    [ "$process_id" != "$$" ] || continue

    executable="$(readlink -f "$process_dir/exe" 2>/dev/null || true)"
    working_dir="$(readlink -f "$process_dir/cwd" 2>/dev/null || true)"
    command_line="$(tr '\0' ' ' < "$process_dir/cmdline" 2>/dev/null || true)"
    is_verireel=0

    case "$executable" in
        "$PROJECT_DIR/.venv/"*) is_verireel=1 ;;
    esac
    case "$command_line" in
        *"$PROJECT_DIR/.venv/bin/"*) is_verireel=1 ;;
    esac
    if [ "$working_dir" = "$PROJECT_DIR" ]; then
        case "$command_line" in
            *".venv/bin/python"*|*".venv/bin/waitress-serve"*) is_verireel=1 ;;
        esac
    fi

    if [ "$is_verireel" -eq 1 ] && kill "$process_id" 2>/dev/null; then
        STOPPED_PIDS="$STOPPED_PIDS $process_id"
    fi
done

if [ -n "$STOPPED_PIDS" ]; then
    sleep 1
    for process_id in $STOPPED_PIDS; do
        if kill -0 "$process_id" 2>/dev/null; then
            kill -9 "$process_id" 2>/dev/null || true
        fi
    done
    echo "[OK] Stopped VeriReel."
else
    echo "[OK] VeriReel was not running."
fi

safe_remove() {
    relative_path="$1"
    target="$PROJECT_DIR/$relative_path"
    case "$target" in
        "$PROJECT_DIR/"*) ;;
        *)
            echo "[ERROR] Refusing to remove a path outside the VeriReel folder: $target"
            exit 1
            ;;
    esac
    if [ -e "$target" ] || [ -L "$target" ]; then
        rm -rf -- "$target" || exit 1
        echo "[REMOVED] $relative_path"
    fi
}

echo "[INFO] Removing downloaded runtime files and local data..."
for relative_path in \
    .venv .bootstrap .env logs __pycache__ .pytest_cache .mypy_cache \
    .ruff_cache .coverage htmlcov; do
    safe_remove "$relative_path"
done

find "$PROJECT_DIR" -type d -name __pycache__ -prune -exec rm -rf -- {} + 2>/dev/null || true
find "$PROJECT_DIR" -type f -name '*.pyc' -delete 2>/dev/null || true

if [ -d "$PROJECT_DIR/temp" ]; then
    find "$PROJECT_DIR/temp" -mindepth 1 -maxdepth 1 ! -name .gitkeep -exec rm -rf -- {} +
fi
find "$PROJECT_DIR" -maxdepth 1 -type f -name 'flask_*.txt' -delete

echo "[OK] Fresh-start cleanup complete. Source code and setup files were preserved."
echo "Run: bash start.sh"
