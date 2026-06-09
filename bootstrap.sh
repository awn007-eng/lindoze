#!/usr/bin/env bash
# Lindoze bootstrap — set up a local venv, install dependencies, and drop a
# start-menu entry + icon into ~/.local/share/. Safe to re-run.
set -e

cd "$(dirname "$(readlink -f "$0")")"
REPO="$(pwd)"

echo ">>> Lindoze bootstrap"
echo ">>> Needs: python3.10+, pip, working internet."
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found."
    echo "  Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    echo "  Fedora/Nobara: sudo dnf install -y python3 python3-virtualenv python3-pip"
    echo "  Arch:          sudo pacman -S --needed python python-pip"
    exit 1
fi

PYV=$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')
echo ">>> Found Python $PYV"

if [ ! -d .venv ]; then
    echo ">>> Creating venv..."
    if ! python3 -m venv .venv 2>/dev/null; then
        echo "venv creation failed. Likely missing the venv module."
        echo "  Ubuntu/Debian: sudo apt install -y python3-venv"
        echo "  Fedora/Nobara: sudo dnf install -y python3-virtualenv"
        exit 1
    fi
fi

echo ">>> Installing dependencies (slow part — ~1-2 min on first run)..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -e .

# --- Desktop integration ---------------------------------------------------
# Install a templated .desktop entry and the bundled icon into the per-user
# XDG locations. Idempotent — re-running just overwrites with fresh paths.
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
mkdir -p "$APPS_DIR" "$ICON_DIR"

if [ -f assets/lindoze.svg ]; then
    cp assets/lindoze.svg "$ICON_DIR/lindoze.svg"
    echo ">>> Installed icon to $ICON_DIR/lindoze.svg"
fi

# Substitute @EXEC_PATH@ with the absolute path to this checkout's run.sh.
sed "s|@EXEC_PATH@|$REPO/run.sh|" packaging/lindoze.desktop \
    > "$APPS_DIR/lindoze.desktop"
chmod 644 "$APPS_DIR/lindoze.desktop"
echo ">>> Installed start-menu entry to $APPS_DIR/lindoze.desktop"

# Best-effort cache refresh — non-fatal if the tools aren't installed.
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$APPS_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 \
    && gtk-update-icon-cache -f -t "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" 2>/dev/null || true

echo
echo ">>> Done. Launch options:"
echo "      ./run.sh                          (from this directory)"
echo "      Start menu → 'Lindoze Process Manager'"
echo
