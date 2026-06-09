"""Startup tab — user-level XDG autostart only.

Scope is deliberately limited to ~/.config/autostart/*.desktop. This is where
third-party apps register "launch on login" entries (Steam, Discord, the
user's own ricing scripts, etc.). Critical session components and the
systemd --user unit graph are out of scope for v1 — they're behind an
"advanced" toggle in v2.

Safety:
- Disable is reversible: we set Hidden=true rather than deleting the file.
- Every write makes a .bak sibling the first time (idempotent).
- An undo toast appears for 10s after each toggle so the "wait, what did
  I just do" moment has a one-click recovery.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


AUTOSTART_DIR = Path.home() / ".config" / "autostart"


@dataclass
class AutostartEntry:
    path: Path
    name: str
    comment: str
    exec_cmd: str
    icon: str
    hidden: bool


# ---- .desktop file parsing / writing
# Hand-rolled instead of configparser so we preserve original formatting,
# blank lines, comments, and key order. We only touch the Hidden= line.

def _parse_desktop(path: Path) -> Optional[AutostartEntry]:
    name = comment = exec_cmd = icon = ""
    hidden = False
    in_entry = False
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                in_entry = (s == "[Desktop Entry]")
                continue
            if not in_entry or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip()
            # Skip locale-suffixed keys like Name[de]= — we only want the
            # untagged default for v1.
            if "[" in k:
                continue
            if k == "Name":
                name = v
            elif k == "Comment":
                comment = v
            elif k == "Exec":
                exec_cmd = v
            elif k == "Icon":
                icon = v
            elif k == "Hidden":
                hidden = v.lower() == "true"
    except (OSError, UnicodeDecodeError):
        return None
    return AutostartEntry(
        path=path,
        name=name or path.stem,
        comment=comment,
        exec_cmd=exec_cmd,
        icon=icon or "application-x-executable",
        hidden=hidden,
    )


def _list_autostart() -> list[AutostartEntry]:
    if not AUTOSTART_DIR.exists():
        return []
    out = []
    for p in sorted(AUTOSTART_DIR.glob("*.desktop")):
        e = _parse_desktop(p)
        if e is not None:
            out.append(e)
    return out


def _set_hidden(path: Path, hidden: bool) -> None:
    """Set or remove Hidden= in the [Desktop Entry] section. Makes a one-time
    .bak before the first write so users can hand-recover."""
    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())

    lines = path.read_text(encoding="utf-8").splitlines()
    in_entry = False
    handled = False
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            # Leaving Desktop Entry without seeing Hidden — add it if needed.
            if in_entry and not handled and hidden:
                out.append("Hidden=true")
                handled = True
            in_entry = (s == "[Desktop Entry]")
            out.append(line)
            continue
        if in_entry and "=" in line and line.split("=", 1)[0].strip() == "Hidden":
            handled = True
            if hidden:
                out.append("Hidden=true")
            # else: drop the line, removing Hidden=
            continue
        out.append(line)
    if in_entry and not handled and hidden:
        out.append("Hidden=true")
    path.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")


def _resolve_icon(icon_str: str) -> QIcon:
    if icon_str and os.path.isabs(icon_str) and os.path.exists(icon_str):
        return QIcon(icon_str)
    if icon_str:
        ic = QIcon.fromTheme(icon_str)
        if not ic.isNull():
            return ic
    return QIcon.fromTheme("application-x-executable")


# ---- Row widget

class _EntryRow(QWidget):
    def __init__(self, entry: AutostartEntry, on_toggle: Callable[[AutostartEntry], None]):
        super().__init__()
        self.entry = entry
        self._on_toggle = on_toggle

        icon_lbl = QLabel()
        icon_lbl.setFixedSize(36, 36)
        pix = _resolve_icon(entry.icon).pixmap(32, 32)
        icon_lbl.setPixmap(pix)
        icon_lbl.setAlignment(Qt.AlignCenter)

        name = QLabel(entry.name)
        nf = QFont(); nf.setPointSize(11); nf.setBold(True)
        name.setFont(nf)

        sub_text = entry.comment or entry.exec_cmd
        sub = QLabel(sub_text)
        sub.setStyleSheet("color: #999; font-size: 9pt;")
        sub.setTextInteractionFlags(Qt.NoTextInteraction)
        sub.setWordWrap(False)
        # Elide manually if too long
        if len(sub_text) > 90:
            sub.setText(sub_text[:87] + "…")
            sub.setToolTip(sub_text)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        text_box.addWidget(name)
        text_box.addWidget(sub)

        self.toggle = QPushButton("Enabled" if not entry.hidden else "Disabled")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(not entry.hidden)
        self.toggle.setFixedWidth(110)
        self.toggle.setCursor(Qt.PointingHandCursor)
        self._restyle_toggle()
        self.toggle.clicked.connect(self._clicked)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.addWidget(icon_lbl)
        root.addLayout(text_box, stretch=1)
        root.addWidget(self.toggle)

    def _restyle_toggle(self) -> None:
        if self.toggle.isChecked():
            self.toggle.setStyleSheet(
                "QPushButton { background: #1e6b3a; color: white; "
                "border: 1px solid #2a8a4d; border-radius: 4px; padding: 6px 12px; } "
                "QPushButton:hover { background: #237a44; }"
            )
        else:
            self.toggle.setStyleSheet(
                "QPushButton { background: #4a2a2a; color: #ddd; "
                "border: 1px solid #6b3838; border-radius: 4px; padding: 6px 12px; } "
                "QPushButton:hover { background: #573030; }"
            )

    def _clicked(self) -> None:
        # Optimistic UI: update label first; let parent perform the file write
        new_hidden = not self.toggle.isChecked()  # checked = enabled, so hidden = !checked
        self.toggle.setText("Enabled" if not new_hidden else "Disabled")
        self._restyle_toggle()
        self._on_toggle(self.entry)


# ---- Undo toast

class _UndoToast(QFrame):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setVisible(False)
        self.setStyleSheet(
            "QFrame { background: #2a2a2a; border: 1px solid #444; "
            "border-radius: 4px; } "
            "QLabel { color: #ddd; } "
            "QPushButton { background: #17a2b8; color: white; border: 0; "
            "border-radius: 3px; padding: 4px 12px; } "
            "QPushButton:hover { background: #1cb6ce; }"
        )
        self.label = QLabel("")
        self.btn = QPushButton("Undo")
        self.btn.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 8, 8)
        lay.addWidget(self.label, stretch=1)
        lay.addWidget(self.btn)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._undo_fn: Optional[Callable[[], None]] = None
        self.btn.clicked.connect(self._do_undo)

    def show_with(self, text: str, undo_fn: Callable[[], None], duration_ms: int = 10000):
        self.label.setText(text)
        self._undo_fn = undo_fn
        self.setVisible(True)
        self._timer.start(duration_ms)

    def _do_undo(self) -> None:
        if self._undo_fn is not None:
            self._undo_fn()
        self._timer.stop()
        self.hide()


# ---- Page

class StartupPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        header = QLabel("Startup apps")
        hf = QFont(); hf.setPointSize(20); hf.setBold(True)
        header.setFont(hf)

        self._sub = QLabel("")  # filled in by reload()
        self._sub.setStyleSheet("color: #999;")

        refresh = QPushButton("Refresh")
        refresh.setStyleSheet(
            "QPushButton { background: #2a2a2a; border: 1px solid #444; "
            "border-radius: 3px; padding: 6px 14px; color: #ddd; } "
            "QPushButton:hover { background: #353535; }"
        )
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.reload)

        top = QHBoxLayout()
        top_text = QVBoxLayout()
        top_text.setContentsMargins(0, 0, 0, 0)
        top_text.setSpacing(2)
        top_text.addWidget(header)
        top_text.addWidget(self._sub)
        top.addLayout(top_text, stretch=1)
        top.addWidget(refresh, alignment=Qt.AlignTop)

        info = QLabel(
            "These applications launch automatically when you log in. "
            "Disabling an entry only prevents it from auto-launching — you can "
            "still run it manually, and the change is reversible at any time."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #888; font-size: 9pt;")

        self.list = QListWidget()
        self.list.setStyleSheet(
            "QListWidget { background: #1a1a1a; border: 1px solid #2a2a2a; "
            "border-radius: 4px; outline: 0; } "
            "QListWidget::item { border-bottom: 1px solid #232323; padding: 0; } "
            "QListWidget::item:selected { background: #252525; }"
        )
        self.list.setSelectionMode(QListWidget.NoSelection)
        self.list.setVerticalScrollMode(QListWidget.ScrollPerPixel)

        self._empty = QLabel(
            "No autostart entries found in ~/.config/autostart/.\n"
            "Apps that auto-launch via systemd user units are not shown here in v1."
        )
        self._empty.setStyleSheet("color: #888; font-size: 11pt;")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setVisible(False)

        self._toast = _UndoToast(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(10)
        root.addLayout(top)
        root.addWidget(info)
        root.addWidget(self.list, stretch=1)
        root.addWidget(self._empty, stretch=1)
        root.addWidget(self._toast)

        self.reload()

    def reload(self) -> None:
        self.list.clear()
        entries = _list_autostart()
        self._sub.setText(f"{AUTOSTART_DIR}   •   {len(entries)} entries")
        if not entries:
            self.list.setVisible(False)
            self._empty.setVisible(True)
            return
        self.list.setVisible(True)
        self._empty.setVisible(False)
        for e in entries:
            row = _EntryRow(e, on_toggle=self._on_toggle)
            item = QListWidgetItem(self.list)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)

    def _on_toggle(self, entry: AutostartEntry) -> None:
        new_hidden = not entry.hidden  # flip
        try:
            _set_hidden(entry.path, new_hidden)
        except OSError as e:
            self._toast.show_with(
                f"Failed to update {entry.name}: {e}",
                undo_fn=lambda: None,
                duration_ms=6000,
            )
            return
        entry.hidden = new_hidden  # keep in-memory in sync

        action = "Disabled" if new_hidden else "Enabled"
        verb = "re-enable" if new_hidden else "re-disable"

        def undo() -> None:
            try:
                _set_hidden(entry.path, not new_hidden)
                entry.hidden = not new_hidden
            except OSError:
                return
            self.reload()

        self._toast.show_with(
            f"{action} {entry.name} at login. Click Undo to {verb}.",
            undo_fn=undo,
            duration_ms=10000,
        )
