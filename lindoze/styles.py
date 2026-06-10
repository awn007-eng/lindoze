"""Shared QSS snippets so toolbar buttons, pill toggles, and other
small widgets stay visually unified across tabs.

Use these instead of inlining the same color codes in each page.
"""
from __future__ import annotations


ACCENT = "#17a2b8"  # Win11 teal-cyan — matches the nav-rail active indicator
ACCENT_HOVER = "#1cb6ce"

# Neutral toolbar button (Refresh, End task, Overall, Performance time-scale
# in unchecked state). Padding is intentionally not set here — callers pick
# their own based on label width so we don't constrain that.
BUTTON_QSS = (
    "QPushButton, QToolButton { "
    "background: #2a2a2a; border: 1px solid #444; border-radius: 3px; "
    "color: #ddd; } "
    "QPushButton:hover, QToolButton:hover { background: #353535; "
    "border-color: #555; } "
    "QPushButton:disabled, QToolButton:disabled { color: #666; "
    "border-color: #3a3a3a; }"
)

# Checkable pill toggle (Performance time-scale 60s / 10min / 1hr).
# Extends BUTTON_QSS with a teal "on" state for the checked variant.
PILL_BUTTON_QSS = (
    BUTTON_QSS
    + " QPushButton:checked, QToolButton:checked { "
    f"background: {ACCENT}; color: white; border: 1px solid {ACCENT}; "
    "} "
    "QPushButton:checked:hover, QToolButton:checked:hover { "
    f"background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; "
    "}"
)
