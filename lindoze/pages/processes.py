"""Processes tab — Win11-style flat tree with two top-level groups
(User / System processes) and per-process actions.

Tree depth is intentionally 2 (group → process). A PID-tree mode is deferred
to v2; htop-style flat-within-group reads better and matches Win11's
behavior closer than a strict PPID tree does.
"""
from __future__ import annotations

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
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..process_sampler import ProcSnap


COLS = [
    ("Name", 280),
    ("PID", 70),
    ("User", 110),
    ("CPU %", 70),
    ("Memory", 100),
    ("Disk", 100),
    ("Status", 90),
    ("Threads", 70),
]

ME = os.getenv("USER") or "aaron"


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

        if role == Qt.FontRole and node.snap is None:
            f = QFont(); f.setBold(True); return f
        if role == Qt.ForegroundRole and node.snap is None:
            return QColor("#9ecaff")
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
        current_set = set(current_pids)
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
        ]
        return getters[col](sl) < getters[col](sr)

    def filterAcceptsRow(self, row, parent_idx):
        if not parent_idx.isValid():
            return True  # always accept group headers
        if not self._needle:
            return True
        src = self.sourceModel()
        parent_node: Node = parent_idx.internalPointer()
        if row >= len(parent_node.children):
            return False
        child = parent_node.children[row]
        snap = child.snap
        if snap is None:
            return True
        n = self._needle
        return n in snap.name.lower() or n in snap.cmdline.lower() or n in str(snap.pid)


class ProcessesPage(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        toolbar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search (name, command line, or PID)")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(
            "QLineEdit { background: #2a2a2a; border: 1px solid #444; "
            "border-radius: 3px; padding: 4px 8px; color: #ddd; }"
        )
        self._end_btn = QPushButton("End task")
        self._end_btn.setStyleSheet(
            "QPushButton { background: #2a2a2a; border: 1px solid #444; "
            "border-radius: 3px; padding: 4px 14px; color: #ddd; } "
            "QPushButton:hover { background: #353535; } "
            "QPushButton:disabled { color: #666; }"
        )
        self._end_btn.setEnabled(False)
        self._end_btn.clicked.connect(self._end_task_selected)
        toolbar.addWidget(self._search, stretch=1)
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
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.header().setSortIndicator(3, Qt.DescendingOrder)  # CPU% desc by default
        self.tree.selectionModel().selectionChanged.connect(self._on_selection)

        self._search.textChanged.connect(self._proxy.set_needle)

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
