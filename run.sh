#!/usr/bin/env bash
# Launcher for Lindoze Process Manager — used by the .desktop entry so KDE
# doesn't need to know about the venv path.
cd "$(dirname "$(readlink -f "$0")")"
exec ./.venv/bin/python -m lindoze "$@"
