"""Processes tab — Win11-style flat tree with two top-level groups
(User / System processes) and per-process actions.

Tree depth is intentionally 2 (group → process). A PID-tree mode is deferred
to v2; htop-style flat-within-group reads better and matches Win11's
behavior closer than a strict PPID tree does.
"""
from __future__ import annotations

import datetime
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
    QSettings,
    QSize,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QActionGroup,
    QColor,
    QFont,
    QFontMetrics,
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
def _make_fonts(point_size: float | None = None):
    """Build the (mono, mono_bold, bold) role fonts, optionally at an explicit
    point size. Compact density mode shrinks these so rows get shorter — the
    numeric columns use their own mono font, so the row only shrinks if these
    shrink alongside the view font, not the view font alone."""
    mono = QFont("Monospace"); mono.setStyleHint(QFont.Monospace)
    mono_bold = QFont("Monospace"); mono_bold.setStyleHint(QFont.Monospace); mono_bold.setBold(True)
    bold = QFont(); bold.setBold(True)
    if point_size and point_size > 0:
        for f in (mono, mono_bold, bold):
            f.setPointSizeF(point_size)
    return mono, mono_bold, bold

_ALIGN_RIGHT_VCENTER = int(Qt.AlignRight | Qt.AlignVCenter)
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
        # View shape: "grouped" = two collapsible User/System groups (default);
        # "flat" = one global list; "user"/"system" = one list filtered by owner.
        # Flat shapes give the proxy a single level to sort, so CPU-descending
        # surfaces the hottest process anywhere instead of per-group.
        self._mode = "grouped"
        self._last_snaps: dict[int, ProcSnap] = {}
        self._mono, self._mono_bold, self._bold = _make_fonts()
        self._v_pad = 3
        self._row_h = self._compute_row_height()

    def _compute_row_height(self) -> int:
        """Pin one row height for the whole view. Columns paint with different
        fonts (proportional names vs. mono numbers) whose line heights differ;
        with uniformRowHeights, Qt would otherwise sample whichever column is
        leftmost-visible and the height would flicker during horizontal scroll
        (tbone's bug). Taking the max of both fonts up front makes it stable."""
        m = max(QFontMetrics(self._mono).height(), QFontMetrics(self._bold).height())
        return m + self._v_pad * 2

    def set_point_size(self, point_size: float | None, v_pad: int = 3) -> None:
        """Rebuild the role fonts at a new size (compact density) and recompute
        the pinned row height. The view relayout that follows re-queries the
        fonts via FontRole and the height via SizeHintRole."""
        self._mono, self._mono_bold, self._bold = _make_fonts(point_size)
        self._v_pad = v_pad
        self._row_h = self._compute_row_height()

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

        if role == Qt.SizeHintRole:
            # Width 0 → let the column/delegate decide width; we only pin height.
            return QSize(0, self._row_h)
        if role == Qt.TextAlignmentRole and col in NUMERIC_COLS:
            return _ALIGN_RIGHT_VCENTER
        if role == Qt.FontRole:
            if col in NUMERIC_COLS:
                return self._mono_bold if node.snap is None else self._mono
            if node.snap is None:
                return self._bold
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

    # ---- View mode
    def set_mode(self, mode: str) -> None:
        """Switch the tree shape ("grouped" | "flat" | "user" | "system").
        A full reset is cheap (it happens only on user action) and avoids
        hand-diffing one shape into another; per-tick updates stay incremental."""
        if mode == self._mode:
            return
        self.beginResetModel()
        self._mode = mode
        self._rebuild_shape()
        self.endResetModel()

    def _leaves_for_mode(self) -> dict[int, ProcSnap]:
        """The leaf snaps a non-grouped mode should show, filtered by owner."""
        if self._mode == "user":
            return {pid: s for pid, s in self._last_snaps.items() if s.user == ME}
        if self._mode == "system":
            return {pid: s for pid, s in self._last_snaps.items() if s.user != ME}
        return dict(self._last_snaps)  # flat: everything

    def _rebuild_shape(self) -> None:
        """Rebuild root.children + parenting from _last_snaps for the current
        mode. Runs inside begin/endResetModel, so it assigns lists directly
        instead of emitting per-row insert/remove signals."""
        if self._mode == "grouped":
            user = {pid: s for pid, s in self._last_snaps.items() if s.user == ME}
            sysd = {pid: s for pid, s in self._last_snaps.items() if s.user != ME}
            self._user_grp.children = [
                Node(pid=p, snap=user[p], group="", parent=self._user_grp)
                for p in sorted(user)
            ]
            self._sys_grp.children = [
                Node(pid=p, snap=sysd[p], group="", parent=self._sys_grp)
                for p in sorted(sysd)
            ]
            self._root.children = [self._user_grp, self._sys_grp]
        else:
            leaves = self._leaves_for_mode()
            self._root.children = [
                Node(pid=p, snap=leaves[p], group="", parent=self._root)
                for p in sorted(leaves)
            ]

    # ---- Updates
    def update(self, snaps: dict[int, ProcSnap]) -> None:
        self._last_snaps = snaps
        if self._mode == "grouped":
            user_snaps = {pid: s for pid, s in snaps.items() if s.user == ME}
            sys_snaps = {pid: s for pid, s in snaps.items() if s.user != ME}
            self._update_group(self._user_grp, 0, user_snaps)
            self._update_group(self._sys_grp, 1, sys_snaps)
        else:
            self._update_container(self._root, QModelIndex(), self._leaves_for_mode())

    def _update_group(self, group: Node, group_row: int, new: dict[int, ProcSnap]) -> None:
        group_idx = self.createIndex(group_row, 0, group)
        self._update_container(group, group_idx, new)
        # Group header aggregates
        self.dataChanged.emit(
            self.createIndex(group_row, 0, group),
            self.createIndex(group_row, len(COLS) - 1, group),
        )

    def _update_container(self, container: Node, parent_index: QModelIndex,
                          new: dict[int, ProcSnap]) -> None:
        """Incrementally diff a container's leaf children against the new snap
        set. `parent_index` is the model index of the container (a group header
        in grouped mode, or the invalid root index in the flat modes)."""
        current_pids = [c.pid for c in container.children]
        new_set = set(new.keys())

        # Remove gone PIDs (from end to start so indices stay valid)
        for i in range(len(current_pids) - 1, -1, -1):
            if current_pids[i] not in new_set:
                self.beginRemoveRows(parent_index, i, i)
                del container.children[i]
                self.endRemoveRows()

        # Add new PIDs (sorted for stable ordering on insertion)
        present = {c.pid for c in container.children}
        to_add = sorted(p for p in new_set if p not in present)
        if to_add:
            start = len(container.children)
            self.beginInsertRows(parent_index, start, start + len(to_add) - 1)
            for pid in to_add:
                container.children.append(
                    Node(pid=pid, snap=new[pid], group="", parent=container))
            self.endInsertRows()

        # Update existing rows — only emit dataChanged when a value the user
        # would actually see has changed. Every-tick repaint of 500 rows was
        # a measurable chunk of the app's CPU cost.
        for i, child in enumerate(container.children):
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


class ProcessProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._needle = ""
        self.setRecursiveFilteringEnabled(True)

    def set_needle(self, s: str) -> None:
        self._needle = s.lower().strip()
        # invalidate() (not the deprecated invalidateFilter/invalidateRowsFilter)
        # re-runs the mapping; the process list is small enough that re-sorting
        # alongside the filter is negligible.
        self.invalidate()

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
        # Resolve the node directly so this works whether the row is a group
        # header, a child under a group (grouped mode), or a top-level leaf
        # (flat / user / system modes) — the old "top-level == header" shortcut
        # broke once leaves could live at the top level.
        idx = self.sourceModel().index(row, 0, parent_idx)
        node: Node = idx.internalPointer()
        if node is None or node.snap is None:
            return True  # group headers stay; recursive filtering keeps matching kids
        if not self._needle:
            return True
        snap = node.snap
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

        # Vertically center the document in the cell the way the default delegate
        # centers its text — otherwise matched cells render top-aligned and sit
        # higher than their unmatched neighbours (tbone's line-height drift),
        # which is most visible when the row is taller than the text.
        y_off = max(0.0, (text_rect.height() - doc.size().height()) / 2)
        painter.save()
        painter.translate(text_rect.left(), text_rect.top() + y_off)
        painter.setClipRect(0, 0, text_rect.width(), text_rect.height())
        doc.documentLayout().draw(painter, ctx)
        painter.restore()


class ProcessesPage(QWidget):
    # (mode key, menu label) — order is the menu order.
    VIEW_MODES = [
        ("grouped", "Grouped"),
        ("flat", "Flat (all)"),
        ("user", "User only"),
        ("system", "System only"),
    ]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        # Set by the window; lets us tell the sampler when the costly exe/cmdline
        # reads are actually needed (Path/Command-line column shown or searching).
        self._sampler = None
        self._detail_needed = False

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
        self._cols_btn = QPushButton("Columns ▾")
        self._cols_btn.setStyleSheet(BUTTON_QSS + " QPushButton { padding: 4px 14px; }")
        self._cols_btn.clicked.connect(self._on_columns_button)
        self._settings = QSettings()
        self._view_mode = self._settings.value("processes/view_mode", "grouped", type=str)
        if self._view_mode not in (m for m, _ in self.VIEW_MODES):
            self._view_mode = "grouped"
        self._view_btn = QPushButton("View ▾")
        self._view_btn.setToolTip("Group by User/System, or show one flat sortable list")
        self._view_btn.setStyleSheet(BUTTON_QSS + " QPushButton { padding: 4px 14px; }")
        self._view_btn.clicked.connect(self._on_view_button)
        self._compact = self._settings.value("processes/compact", False, type=bool)
        self._base_pt = QApplication.font().pointSizeF()
        if self._base_pt <= 0:
            self._base_pt = 10.0
        self._compact_btn = QPushButton()
        self._compact_btn.setToolTip("Toggle row density — more processes per screen")
        self._compact_btn.setStyleSheet(BUTTON_QSS + " QPushButton { padding: 4px 14px; }")
        self._compact_btn.clicked.connect(self._on_compact_clicked)
        self._end_btn = QPushButton("End task")
        self._end_btn.setStyleSheet(BUTTON_QSS + " QPushButton { padding: 4px 14px; }")
        self._end_btn.setEnabled(False)
        self._end_btn.clicked.connect(self._end_task_selected)
        toolbar.addWidget(self._search, stretch=1)
        toolbar.addWidget(self._match_label)
        toolbar.addWidget(self._compact_btn)
        toolbar.addWidget(self._view_btn)
        toolbar.addWidget(self._cols_btn)
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
        # Ctrl/Shift-click to select several processes and act on them at once
        # (batch End/Kill). Single-click still selects one row as before.
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setRootIsDecorated(True)
        self.tree.setExpandsOnDoubleClick(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self._apply_density()
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

        # Apply the persisted view mode (grouped is the model default, so only
        # a non-default needs switching). Safe before any snapshot — the reset
        # just builds an empty shape that the first update() fills.
        if self._view_mode != "grouped":
            self._model.set_mode(self._view_mode)

        # Expand both groups by default after first snapshot (grouped mode only).
        self._expanded_once = False

    # ---- Sampler hook
    def on_snapshot(self, snaps: dict[int, ProcSnap]) -> None:
        self._model.update(snaps)
        if (self._view_mode == "grouped" and not self._expanded_once
                and self._model.rowCount() >= 2):
            self._expand_groups()
            self._expanded_once = True
        if self._search.text().strip():
            self._on_search_changed(self._search.text())

    def _expand_groups(self) -> None:
        for r in range(self._proxy.rowCount()):
            self.tree.expand(self._proxy.index(r, 0))

    # ---- View mode
    def _view_menu(self) -> QMenu:
        menu = QMenu(self)
        group = QActionGroup(menu)
        group.setExclusive(True)
        for mode, label in self.VIEW_MODES:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(mode == self._view_mode)
            group.addAction(act)
            act.triggered.connect(lambda _checked=False, m=mode: self._set_view_mode(m))
        return menu

    def _on_view_button(self) -> None:
        btn = self._view_btn
        self._view_menu().exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _set_view_mode(self, mode: str) -> None:
        if mode == self._view_mode:
            return
        self._view_mode = mode
        self._settings.setValue("processes/view_mode", mode)
        self._model.set_mode(mode)
        if mode == "grouped":
            # Fresh group parents to expand once the next snapshot arrives.
            self._expanded_once = False
            self._expand_groups()
        # The reset re-runs filtering; refresh the match count to match.
        if self._search.text().strip():
            self._on_search_changed(self._search.text())
        else:
            self._match_label.setText("")

    # ---- Header column show/hide
    def _columns_menu(self) -> QMenu:
        menu = QMenu(self)
        for i, (label, _) in enumerate(COLS):
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(not self.tree.isColumnHidden(i))
            # Keep Name (col 0) always visible — it's the primary identifier.
            if i == 0:
                act.setEnabled(False)
            act.toggled.connect(
                lambda checked, col=i: self._toggle_column(col, checked)
            )
        return menu

    def _toggle_column(self, col: int, visible: bool) -> None:
        self.tree.setColumnHidden(col, not visible)
        self._update_detail_needed()

    def _on_columns_button(self) -> None:
        # Reliable, discoverable entry point — drops the menu under the button.
        btn = self._cols_btn
        self._columns_menu().exec(btn.mapToGlobal(btn.rect().bottomLeft()))

    def _on_header_menu(self, pos) -> None:
        # Bonus right-click affordance; on some sessions the header doesn't
        # emit this, which is why the toolbar button above is the primary path.
        self._columns_menu().exec(self.tree.header().mapToGlobal(pos))

    # ---- Row density
    def _apply_density(self) -> None:
        # Compact tightens vertical padding only — the font size stays put.
        # (tbone was explicit: he wants less padding, not micro-fonts.) Row
        # height is max(role-font height) + 2*v_pad, so dropping v_pad alone
        # shortens rows while keeping text fully legible.
        v_pad = 1 if self._compact else 3
        self._model.set_point_size(self._base_pt, v_pad)
        view_font = self.tree.font()
        view_font.setPointSizeF(self._base_pt)
        self.tree.setFont(view_font)

        # Horizontal padding only — the model's SizeHintRole owns row height now,
        # so vertical padding here would just fight it. Keep a hairline so text
        # isn't flush against the column edge.
        self.tree.setStyleSheet(
            "QTreeView { background: #1a1a1a; alternate-background-color: #1f1f1f; "
            "color: #ddd; border: none; selection-background-color: #2d4a5a; } "
            "QTreeView::item { padding: 0px 4px; } "
            "QHeaderView::section { background: #232323; color: #ccc; "
            "padding: 6px 8px; border: 0; border-right: 1px solid #2a2a2a; }"
        )
        # The model's row height changed; force the view to re-read SizeHintRole
        # now instead of waiting for the next model reset.
        self.tree.doItemsLayout()
        self.tree.viewport().update()
        # Label names the action the button performs (what it switches to),
        # not the current state — consistent with the app's other toggles.
        self._compact_btn.setText("Standard" if self._compact else "Compact")

    def _on_compact_clicked(self) -> None:
        self._compact = not self._compact
        self._settings.setValue("processes/compact", self._compact)
        self._apply_density()

    # ---- Detail gating (exe/cmdline are only read when shown or searched)
    def set_sampler(self, sampler) -> None:
        self._sampler = sampler
        self._update_detail_needed()

    def _update_detail_needed(self) -> None:
        if self._sampler is None:
            return
        needed = (not self.tree.isColumnHidden(8)
                  or not self.tree.isColumnHidden(9)
                  or bool(self._search.text().strip()))
        was = self._detail_needed
        self._detail_needed = needed
        self._sampler.set_detail_needed(needed)
        # Going from off->on: backfill exe/cmdline now so the column/search
        # populates immediately instead of after the next tick.
        if needed and not was:
            self._sampler.refresh_now()

    # ---- Search
    def _on_search_changed(self, text: str) -> None:
        self._proxy.set_needle(text)
        self._highlight.set_needle(text)
        self.tree.viewport().update()
        self._update_detail_needed()
        if not text.strip():
            self._match_label.setText("")
            return
        count = self._visible_match_count()
        self._match_label.setText(f"{count} match" if count == 1 else f"{count} matches")

    def _visible_match_count(self) -> int:
        """Count visible process leaves across the whole tree — correct whether
        leaves sit under group headers (grouped) or at the top level (flat)."""
        def walk(parent_idx) -> int:
            total = 0
            for r in range(self._proxy.rowCount(parent_idx)):
                idx = self._proxy.index(r, 0, parent_idx)
                node = self._proxy.mapToSource(idx).internalPointer()
                if node is not None and node.snap is not None:
                    total += 1
                total += walk(idx)
            return total
        return walk(QModelIndex())

    # ---- Selection
    def _on_selection(self, *_args) -> None:
        owned = [s for s in self._selected_snaps() if s.user == ME]
        self._end_btn.setEnabled(bool(owned))
        # Name the count so it's clear the button acts on the whole selection.
        self._end_btn.setText("End task" if len(owned) <= 1 else f"End {len(owned)} tasks")

    def _selected_snaps(self) -> list[ProcSnap]:
        """Every selected process leaf (group headers excluded)."""
        snaps: list[ProcSnap] = []
        for idx in self.tree.selectionModel().selectedRows():
            node: Node = self._proxy.mapToSource(idx).internalPointer()
            if node is not None and node.snap is not None:
                snaps.append(node.snap)
        return snaps

    # ---- Context menu
    def _on_context_menu(self, pos) -> None:
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return
        # Right-clicking a row outside the current multi-selection focuses just
        # that row; clicking within an existing selection keeps it intact so the
        # batch actions operate on everything highlighted.
        if not self.tree.selectionModel().isRowSelected(idx.row(), idx.parent()):
            self.tree.setCurrentIndex(idx)

        snaps = self._selected_snaps()
        if not snaps:
            return
        if len(snaps) > 1:
            self._batch_context_menu(snaps, pos)
            return
        snap = snaps[0]
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
        a_cpath = m.addAction("Copy path")
        a_cpath.setEnabled(bool(snap.exe))
        a_cpath.triggered.connect(lambda: self._to_clipboard(snap.exe))
        m.addAction("Copy PID").triggered.connect(
            lambda: self._to_clipboard(str(snap.pid)))
        a_ccmd = m.addAction("Copy command line")
        a_ccmd.setEnabled(bool(snap.cmdline))
        a_ccmd.triggered.connect(lambda: self._to_clipboard(snap.cmdline))

        m.addSeparator()
        m.addAction("Open file location").triggered.connect(lambda: self._open_loc(snap.pid))
        m.addAction("Properties…").triggered.connect(lambda: self._properties(snap.pid))

        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _batch_context_menu(self, snaps: list[ProcSnap], pos) -> None:
        owned = [s for s in snaps if s.user == ME]
        n = len(owned)
        plural = "s" if n != 1 else ""
        m = QMenu(self)
        a_end = m.addAction(f"End {n} task{plural}")
        a_end.setEnabled(bool(owned))
        a_end.triggered.connect(
            lambda: self._signal_pids([s.pid for s in owned], signal.SIGTERM, confirm=True))
        a_kill = m.addAction(f"Kill {n} task{plural} (SIGKILL)")
        a_kill.setEnabled(bool(owned))
        a_kill.triggered.connect(
            lambda: self._signal_pids([s.pid for s in owned], signal.SIGKILL, confirm=True))

        # Suspend/Resume act on the applicable subset of the selection: SIGSTOP
        # to owned running processes, SIGCONT to owned stopped ones. Offering
        # both (each enabled only when it has targets) keeps mixed selections
        # predictable instead of guessing a single toggle direction.
        running = [s.pid for s in owned if s.status != "stopped"]
        stopped = [s.pid for s in owned if s.status == "stopped"]
        a_sus = m.addAction(f"Suspend {len(running)} task{'s' if len(running) != 1 else ''}")
        a_sus.setEnabled(bool(running))
        a_sus.triggered.connect(
            lambda: self._signal_pids(running, signal.SIGSTOP, confirm=False))
        a_res = m.addAction(f"Resume {len(stopped)} task{'s' if len(stopped) != 1 else ''}")
        a_res.setEnabled(bool(stopped))
        a_res.triggered.connect(
            lambda: self._signal_pids(stopped, signal.SIGCONT, confirm=False))

        m.addSeparator()
        owned_pids = [s.pid for s in owned]
        pri = m.addMenu("Set priority")
        pri.setEnabled(bool(owned))
        for label, nv in [("Realtime (nice -20)", -20),
                          ("High (-10)", -10),
                          ("Above Normal (-5)", -5),
                          ("Normal (0)", 0),
                          ("Below Normal (5)", 5),
                          ("Low (10)", 10)]:
            act = pri.addAction(label)
            act.triggered.connect(
                lambda _checked=False, p=list(owned_pids), nval=nv: self._renice_many(p, nval))

        # Copy is read-only, so it works on the whole selection regardless of
        # ownership and yields one value per line (the multi-line clipboard
        # tbone expected).
        m.addSeparator()
        a_cpid = m.addAction("Copy PIDs")
        a_cpid.triggered.connect(
            lambda: self._to_clipboard("\n".join(str(s.pid) for s in snaps)))
        paths = [s.exe for s in snaps if s.exe]
        a_cpath = m.addAction("Copy paths")
        a_cpath.setEnabled(bool(paths))
        a_cpath.triggered.connect(lambda: self._to_clipboard("\n".join(paths)))
        cmds = [s.cmdline for s in snaps if s.cmdline]
        a_ccmd = m.addAction("Copy command lines")
        a_ccmd.setEnabled(bool(cmds))
        a_ccmd.triggered.connect(lambda: self._to_clipboard("\n".join(cmds)))

        skipped = len(snaps) - n
        if skipped:
            m.addSeparator()
            info = m.addAction(f"{skipped} selected not owned by you — skipped")
            info.setEnabled(False)
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _to_clipboard(self, text: str) -> None:
        QApplication.clipboard().setText(text or "")

    # ---- Actions
    def _signal_pid(self, pid: int, sig: int, confirm: bool) -> None:
        self._signal_pids([pid], sig, confirm)

    def _signal_pids(self, pids: list[int], sig: int, confirm: bool) -> None:
        pids = list(pids)
        if not pids:
            return
        if confirm:
            subj = "the process" if len(pids) == 1 else "them"
            target = f"PID {pids[0]}" if len(pids) == 1 else f"{len(pids)} processes"
            if sig == signal.SIGKILL:
                body = (f"Send SIGKILL to {target}? This forcibly terminates "
                        f"{subj} and unsaved data may be lost.")
            else:
                body = f"End {target}? Running processes will be asked to terminate."
            ans = QMessageBox.question(
                self, "Confirm", body,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if ans != QMessageBox.Yes:
                return
        denied: list[int] = []
        for pid in pids:
            try:
                os.kill(pid, sig)
            except ProcessLookupError:
                pass  # already gone
            except PermissionError:
                denied.append(pid)
        if denied:
            QMessageBox.warning(
                self, "Permission denied",
                "Could not signal PID(s): " + ", ".join(str(p) for p in denied),
            )

    def _renice(self, pid: int, nice: int) -> None:
        self._renice_many([pid], nice)

    def _renice_many(self, pids: list[int], nice: int) -> None:
        denied: list[int] = []
        for pid in pids:
            try:
                os.setpriority(os.PRIO_PROCESS, pid, nice)
            except PermissionError:
                denied.append(pid)
            except ProcessLookupError:
                pass  # already gone
        if denied:
            QMessageBox.warning(
                self, "Permission denied",
                f"Cannot set nice={nice} for PID(s): "
                + ", ".join(str(p) for p in denied)
                + ".\nNegative nice values require root.",
            )

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
        except psutil.Error as e:
            QMessageBox.information(
                self, f"Properties — PID {pid}", f"Cannot open process: {e}"
            )
            return

        # Read each field independently so one restricted attribute (common for
        # system helpers like (sd-pam)) doesn't sink the whole dialog.
        def field(fn) -> str:
            try:
                return str(fn())
            except psutil.AccessDenied:
                return "(restricted)"
            except psutil.NoSuchProcess:
                return "(no longer running)"
            except (psutil.Error, OSError, ValueError):
                return "?"

        rows = [
            ("PID", str(pid)),
            ("Name", field(p.name)),
            ("Executable", field(p.exe) or "?"),
            ("Working dir", field(p.cwd)),
            ("Command line", field(lambda: " ".join(p.cmdline()))),
            ("User", field(p.username)),
            ("PPID", field(p.ppid)),
            ("Started", field(
                lambda: datetime.datetime.fromtimestamp(
                    p.create_time()).strftime("%Y-%m-%d %H:%M:%S"))),
            ("Threads", field(p.num_threads)),
            ("Open files", field(lambda: len(p.open_files()))),
            ("Status", field(p.status)),
        ]
        text = "\n".join(f"{k}: {v}" for k, v in rows)
        QMessageBox.information(self, f"Properties — PID {pid}", text)

    def _end_task_selected(self) -> None:
        pids = [s.pid for s in self._selected_snaps() if s.user == ME]
        if not pids:
            return
        # Confirm only when ending several at once — single End stays one-click.
        self._signal_pids(pids, signal.SIGTERM, confirm=len(pids) > 1)
