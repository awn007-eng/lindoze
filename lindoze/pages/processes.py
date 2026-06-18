"""Processes tab — Win11-style flat tree with two top-level groups
(User / System processes) and per-process actions.

Tree depth is intentionally 2 (group → process). A PID-tree mode is deferred
to v2; htop-style flat-within-group reads better and matches Win11's
behavior closer than a strict PPID tree does.
"""
from __future__ import annotations

import html as _html
import os
import signal
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import psutil
from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFont,
    QPalette,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..process_sampler import ProcSnap
from ..styles import BUTTON_QSS


COLS = [
    ("Name", 280),
    ("PID", 70),
    ("User", 110),
    ("CPU %", 70),
    ("Memory", 100),
    ("Disk", 100),
    ("Status", 90),
    ("Threads", 70),
    ("Path", 220),
    ("Command line", 360),
]

# Columns hidden on first run — Path and Command line are wide and most users
# don't need them by default. Toggle via the header right-click menu; the
# choice persists through QHeaderView.saveState() (see window.py).
DEFAULT_HIDDEN_COLS = {8, 9}

ME = os.getenv("USER") or "aaron"

# PID, CPU %, Memory, Disk, Threads — numeric columns get right-aligned
# tabular figures so digits and unit suffixes line up vertically.
NUMERIC_COLS = {1, 3, 4, 5, 7}

# Module-level cached fonts/colors. data() is called for every visible cell on
# every dataChanged emission — allocating fresh QFont / QColor objects per call
# was a measurable chunk of UI thread time on the Processes tab.
def _build_role_cache():
    mono = QFont("Monospace"); mono.setStyleHint(QFont.Monospace)
    mono_bold = QFont("Monospace"); mono_bold.setStyleHint(QFont.Monospace); mono_bold.setBold(True)
    bold = QFont(); bold.setBold(True)
    align_right_vcenter = int(Qt.AlignRight | Qt.AlignVCenter)
    return mono, mono_bold, bold, align_right_vcenter

_FONT_MONO, _FONT_MONO_BOLD, _FONT_BOLD, _ALIGN_RIGHT_VCENTER = _build_role_cache()
_FG_GROUP = QColor("#9ecaff")
_BG_GROUP = QColor(23, 162, 184, 32)


def _fmt_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0; v = float(n)
    while v >= 1024 and i < len(units) - 1:
        v /= 1024; i += 1
    return f"{v:.1f} {units[i]}"


def _fmt_bps(n: float) -> str:
    if n < 1:
        return "0 B/s"
    units = ["B/s", "KB/s", "MB/s", "GB/s"]
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024; i += 1
    return f"{n:.1f} {units[i]}"


@dataclass
class Node:
    pid: int  # -1 for group headers
    snap: Optional[ProcSnap]
    group: str  # "user" | "system" | "root" | "" (leaf)
    parent: Optional["Node"] = None
    children: list["Node"] = field(default_factory=list)


class ProcessModel(QAbstractItemModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._root = Node(pid=-2, snap=None, group="root")
        self._user_grp = Node(pid=-1, snap=None, group="user", parent=self._root)
        self._sys_grp = Node(pid=-1, snap=None, group="system", parent=self._root)
        self._root.children = [self._user_grp, self._sys_grp]

    # ---- QAbstractItemModel
    def rowCount(self, parent=QModelIndex()):
        return len(self._node(parent).children)

    def columnCount(self, parent=QModelIndex()):
        return len(COLS)

    def index(self, row, col, parent=QModelIndex()):
        if row < 0 or col < 0 or col >= len(COLS):
            return QModelIndex()
        node = self._node(parent)
        if row >= len(node.children):
            return QModelIndex()
        return self.createIndex(row, col, node.children[row])

    def parent(self, idx):
        if not idx.isValid():
            return QModelIndex()
        node: Node = idx.internalPointer()
        if node.parent is None or node.parent is self._root:
            return QModelIndex()
        grand = node.parent.parent
        row = grand.children.index(node.parent) if grand else 0
        return self.createIndex(row, 0, node.parent)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLS[section][0]
        return None

    def data(self, idx, role=Qt.DisplayRole):
        if not idx.isValid():
            return None
        node: Node = idx.internalPointer()
        col = idx.column()

        if role == Qt.TextAlignmentRole and col in NUMERIC_COLS:
            return _ALIGN_RIGHT_VCENTER
        if role == Qt.FontRole:
            if col in NUMERIC_COLS:
                return _FONT_MONO_BOLD if node.snap is None else _FONT_MONO
            if node.snap is None:
                return _FONT_BOLD
        if role == Qt.ForegroundRole and node.snap is None:
            return _FG_GROUP
        if role == Qt.BackgroundRole and node.snap is None:
            return _BG_GROUP
        if role == Qt.DisplayRole:
            if node.snap is None:
                if col == 0:
                    label = "User processes" if node.group == "user" else "System processes"
                    return f"{label}  ({len(node.children)})"
                if col == 3:  # CPU
                    return f"{self._sum(node, 'cpu_pct'):.0f}%"
                if col == 4:  # Mem
                    return _fmt_bytes(int(self._sum(node, 'mem_rss')))
                if col == 5:  # Disk
                    return _fmt_bps(self._sum(node, 'disk_bps'))
                return ""
            s = node.snap
            return {
                0: s.name,
                1: str(s.pid),
                2: s.user,
                3: f"{s.cpu_pct:.1f}",
                4: _fmt_bytes(s.mem_rss),
                5: _fmt_bps(s.disk_bps),
                6: s.status,
                7: str(s.threads),
                8: s.exe,
                9: s.cmdline,
            }.get(col, "")
        return None

    def _sum(self, group_node: Node, attr: str) -> float:
        return sum(getattr(c.snap, attr) for c in group_node.children if c.snap)

    def _node(self, idx: QModelIndex) -> Node:
        return idx.internalPointer() if idx.isValid() else self._root

    # ---- Updates
    def update(self, snaps: dict[int, ProcSnap]) -> None:
        user_snaps = {pid: s for pid, s in snaps.items() if s.user == ME}
        sys_snaps = {pid: s for pid, s in snaps.items() if s.user != ME}
        self._update_group(self._user_grp, 0, user_snaps)
        self._update_group(self._sys_grp, 1, sys_snaps)

    def _update_group(self, group: Node, group_row: int, new: dict[int, ProcSnap]) -> None:
        current_pids = [c.pid for c in group.children]
        new_set = set(new.keys())

        group_idx = self.createIndex(group_row, 0, group)

        # Remove gone PIDs (from end to start so indices stay valid)
        for i in range(len(current_pids) - 1, -1, -1):
            if current_pids[i] not in new_set:
                self.beginRemoveRows(group_idx, i, i)
                del group.children[i]
                self.endRemoveRows()

        # Add new PIDs (sorted for stable ordering on insertion)
        present = {c.pid for c in group.children}
        to_add = sorted(p for p in new_set if p not in present)
        if to_add:
            start = len(group.children)
            self.beginInsertRows(group_idx, start, start + len(to_add) - 1)
            for pid in to_add:
                group.children.append(Node(pid=pid, snap=new[pid], group="", parent=group))
            self.endInsertRows()

        # Update existing rows — only emit dataChanged when a value the user
        # would actually see has changed. Every-tick repaint of 500 rows was
        # a measurable chunk of the app's CPU cost.
        for i, child in enumerate(group.children):
            ns = new.get(child.pid)
            if ns is None or child.snap is None:
                continue
            old = child.snap
            child.snap = ns
            if (ns.cpu_pct == old.cpu_pct
                    and ns.mem_rss == old.mem_rss
                    and ns.disk_bps == old.disk_bps
                    and ns.status == old.status
                    and ns.threads == old.threads
                    and ns.name == old.name):
                continue
            left = self.createIndex(i, 0, child)
            right = self.createIndex(i, len(COLS) - 1, child)
            self.dataChanged.emit(left, right)

        # Group header aggregates
        self.dataChanged.emit(
            self.createIndex(group_row, 0, group),
            self.createIndex(group_row, len(COLS) - 1, group),
        )


class ProcessProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""
        self.setRecursiveFilteringEnabled(True)

    def set_needle(self, s: str) -> None:
        self._needle = s.lower().strip()
        self.invalidateFilter()

    def lessThan(self, left, right):
        ln: Node = left.internalPointer()
        rn: Node = right.internalPointer()
        # Don't reorder group headers.
        if ln.snap is None or rn.snap is None:
            return False
        col = left.column()
        sl, sr = ln.snap, rn.snap
        getters = [
            lambda s: s.name.lower(),
            lambda s: s.pid,
            lambda s: s.user,
            lambda s: s.cpu_pct,
            lambda s: s.mem_rss,
            lambda s: s.disk_bps,
            lambda s: s.status,
            lambda s: s.threads,
            lambda s: s.exe.lower(),
            lambda s: s.cmdline.lower(),
        ]
        return getters[col](sl) < getters[col](sr)

    def filterAcceptsRow(self, row, parent_idx):
        if not parent_idx.isValid():
            return True  # always accept group headers
        if not self._needle:
            return True
        parent_node: Node = parent_idx.internalPointer()
        if row >= len(parent_node.children):
            return False
        child = parent_node.children[row]
        snap = child.snap
        if snap is None:
            return True
        n = self._needle
        return (n in snap.name.lower()
                or n in snap.exe.lower()
                or n in snap.cmdline.lower()
                or n in str(snap.pid))


class HighlightDelegate(QStyledItemDelegate):
    """Render the search needle in bold accent color within a cell.

    Falls through to the default delegate when there's no needle or the cell
    doesn't contain a match — so non-matching cells (including most group
    headers) keep their normal styling and font-role overrides.
    """

    ACCENT = "#17a2b8"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""

    def set_needle(self, s: str) -> None:
        self._needle = s.lower().strip()

    def paint(self, painter, option, index):
        text = index.data(Qt.DisplayRole)
        if not text or not self._needle:
            return super().paint(painter, option, index)
        t = str(text)
        low = t.lower()
        n = self._needle
        if n not in low:
            return super().paint(painter, option, index)

        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = (opt.widget.style() if opt.widget else QApplication.style())
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)

        parts: list[str] = []
        i = 0
        while i < len(t):
            j = low.find(n, i)
            if j < 0:
                parts.append(_html.escape(t[i:]))
                break
            if j > i:
                parts.append(_html.escape(t[i:j]))
            parts.append(
                f'<span style="color:{self.ACCENT};font-weight:700;">'
                f'{_html.escape(t[j:j + len(n)])}</span>'
            )
            i = j + len(n)

        doc = QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setDocumentMargin(0)
        doc.setHtml("".join(parts))

        text_rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, opt.widget)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        if opt.state & QStyle.State_Selected:
            ctx.palette.setColor(QPalette.Text, opt.palette.color(QPalette.HighlightedText))
        else:
            ctx.palette.setColor(QPalette.Text, opt.palette.color(QPalette.Text))

        painter.save()
        painter.translate(text_rect.topLeft())
        painter.setClipRect(0, 0, text_rect.width(), text_rect.height())
        doc.documentLayout().draw(painter, ctx)
        painter.restore()


class ProcessesPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        toolbar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search (name, path, command line, or PID)")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            "QLineEdit { background: #2a2a2a; border: 1px solid #444; "
            "border-radius: 3px; padding: 4px 8px; color: #ddd; }"
        )
        self._match_label = QLabel("")
        self._match_label.setStyleSheet("QLabel { color: #888; padding: 0 8px; }")
        self._end_btn = QPushButton("End task")
        self._end_btn.setStyleSheet(BUTTON_QSS + " QPushButton { padding: 4px 14px; }")
        self._end_btn.setEnabled(False)
        self._end_btn.clicked.connect(self._end_task_selected)
        toolbar.addWidget(self._search, stretch=1)
        toolbar.addWidget(self._match_label)
        toolbar.addWidget(self._end_btn)

        self._model = ProcessModel(self)
        self._proxy = ProcessProxy(self)
        self._proxy.setSourceModel(self._model)

        self.tree = QTreeView()
        self.tree.setModel(self._proxy)
        self.tree.setSortingEnabled(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tree.setRootIsDecorated(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.setStyleSheet(
            "QTreeView { background: #1a1a1a; alternate-background-color: #1f1f1f; "
            "color: #ddd; border: none; selection-background-color: #2d4a5a; } "
            "QTreeView::item { padding: 3px 4px; } "
            "QHeaderView::section { background: #232323; color: #ccc; "
            "padding: 6px 8px; border: 0; border-right: 1px solid #2a2a2a; }"
        )
        # Column widths
        for i, (_, w) in enumerate(COLS):
            self.tree.setColumnWidth(i, w)
        for i in DEFAULT_HIDDEN_COLS:
            self.tree.setColumnHidden(i, True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSortIndicator(3, Qt.DescendingOrder)  # CPU% desc by default
        # Right-click the header to show/hide columns (Win11-style).
        self.tree.header().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.header().customContextMenuRequested.connect(self._on_header_menu)
        self.tree.selectionModel().selectionChanged.connect(self._on_selection)

        self._highlight = HighlightDelegate(self.tree)
        for c in (0, 1, 8, 9):
            self.tree.setItemDelegateForColumn(c, self._highlight)

        self._search.textChanged.connect(self._on_search_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addLayout(toolbar)
        root.addWidget(self.tree)

        # Expand both groups by default after first snapshot.
        self._expanded_once = False

    # ---- Sampler hook
    def on_snapshot(self, snaps: dict[int, ProcSnap]) -> None:
        self._model.update(snaps)
        if not self._expanded_once and self._model.rowCount() >= 2:
            for r in range(self._proxy.rowCount()):
                self.tree.expand(self._proxy.index(r, 0))
            self._expanded_once = True
        if self._search.text().strip():
            self._on_search_changed(self._search.text())

    # ---- Header column show/hide
    def _on_header_menu(self, pos) -> None:
        menu = QMenu(self)
        for i, (label, _) in enumerate(COLS):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(not self.tree.isColumnHidden(i))
            # Keep Name (col 0) always visible — it's the primary identifier.
            if i == 0:
                act.setEnabled(False)
            act.toggled.connect(
                lambda checked, col=i: self.tree.setColumnHidden(col, not checked)
            )
        menu.exec(self.tree.header().mapToGlobal(pos))

    # ---- Search
    def _on_search_changed(self, text: str) -> None:
        self._proxy.set_needle(text)
        self._highlight.set_needle(text)
        self.tree.viewport().update()
        if not text.strip():
            self._match_label.setText("")
            return
        count = 0
        for r in range(self._proxy.rowCount()):
            count += self._proxy.rowCount(self._proxy.index(r, 0))
        self._match_label.setText(f"{count} match" if count == 1 else f"{count} matches")

    # ---- Selection
    def _on_selection(self, *_args) -> None:
        snap = self._selected_snap()
        self._end_btn.setEnabled(bool(snap and snap.user == ME))

    def _selected_snap(self) -> Optional[ProcSnap]:
        idx = self.tree.currentIndex()
        if not idx.isValid():
            return None
        src = self._proxy.mapToSource(idx)
        node: Node = src.internalPointer()
        return node.snap

    # ---- Context menu
    def _on_context_menu(self, pos) -> None:
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        self.tree.setCurrentIndex(idx)
        snap = self._selected_snap()
        if snap is None:
            return
        owned = snap.user == ME

        m = QMenu(self)
        a_end = m.addAction("End task")
        a_end.setEnabled(owned)
        a_end.triggered.connect(lambda: self._signal_pid(snap.pid, signal.SIGTERM, confirm=False))

        a_kill = m.addAction("Kill (SIGKILL)")
        a_kill.setEnabled(owned)
        a_kill.triggered.connect(lambda: self._signal_pid(snap.pid, signal.SIGKILL, confirm=True))

        is_stopped = snap.status == "stopped"
        a_sus = m.addAction("Resume" if is_stopped else "Suspend")
        a_sus.setEnabled(owned)
        sig = signal.SIGCONT if is_stopped else signal.SIGSTOP
        a_sus.triggered.connect(lambda: self._signal_pid(snap.pid, sig, confirm=False))

        m.addSeparator()
        pri = m.addMenu("Set priority")
        pri.setEnabled(owned)
        for label, nv in [("Realtime (nice -20)", -20),
                          ("High (-10)", -10),
                          ("Above Normal (-5)", -5),
                          ("Normal (0)", 0),
                          ("Below Normal (5)", 5),
                          ("Low (10)", 10)]:
            act = pri.addAction(label)
            act.triggered.connect(lambda _checked=False, p=snap.pid, n=nv: self._renice(p, n))

        m.addSeparator()
        m.addAction("Open file location").triggered.connect(lambda: self._open_loc(snap.pid))
        m.addAction("Properties…").triggered.connect(lambda: self._properties(snap.pid))

        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ---- Actions
    def _signal_pid(self, pid: int, sig: int, confirm: bool) -> None:
        if confirm:
            ans = QMessageBox.question(
                self, "Confirm",
                f"Send SIGKILL to PID {pid}? This forcibly terminates the process "
                f"and unsaved data may be lost.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass  # already gone
        except PermissionError as e:
            QMessageBox.warning(self, "Permission denied", str(e))

    def _renice(self, pid: int, nice: int) -> None:
        try:
            os.setpriority(os.PRIO_PROCESS, pid, nice)
        except PermissionError:
            QMessageBox.warning(
                self, "Permission denied",
                f"Cannot set nice={nice} for PID {pid}. Negative nice values require root.",
            )
        except ProcessLookupError:
            pass

    def _open_loc(self, pid: int) -> None:
        try:
            exe = psutil.Process(pid).exe()
            if exe:
                subprocess.Popen(["xdg-open", os.path.dirname(exe)])
        except (psutil.Error, FileNotFoundError) as e:
            QMessageBox.warning(self, "Cannot open location", str(e))

    def _properties(self, pid: int) -> None:
        try:
            p = psutil.Process(pid)
            text = (
                f"PID: {p.pid}\n"
                f"Name: {p.name()}\n"
                f"Executable: {p.exe() or '?'}\n"
                f"Working dir: {p.cwd() if p.is_running() else '?'}\n"
                f"Command line: {' '.join(p.cmdline()) or p.name()}\n"
                f"User: {p.username()}\n"
                f"PPID: {p.ppid()}\n"
                f"Started: {__import__('datetime').datetime.fromtimestamp(p.create_time())}\n"
                f"Threads: {p.num_threads()}\n"
                f"Open files: {len(p.open_files()) if p.is_running() else '?'}\n"
                f"Status: {p.status()}"
            )
        except psutil.Error as e:
            text = f"Error reading process: {e}"
        QMessageBox.information(self, f"Properties — PID {pid}", text)

    def _end_task_selected(self) -> None:
        snap = self._selected_snap()
        if snap:
            self._signal_pid(snap.pid, signal.SIGTERM, confirm=False)
