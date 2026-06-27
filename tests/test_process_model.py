"""View-mode + filter tests for the Processes model/proxy.

These exercise the tree-shape switching (grouped vs. flat/user/system) and the
proxy's needle filter without real /proc data — synthetic ProcSnaps stand in.
A QApplication is needed because the model builds QFonts in __init__.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QApplication

from lindoze.pages.processes import ME, ProcessModel, ProcessProxy
from lindoze.process_sampler import ProcSnap


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def _snap(pid: int, user: str, name: str = "proc", cpu: float = 0.0,
          exe: str = "", cmdline: str = "", status: str = "running") -> ProcSnap:
    return ProcSnap(
        pid=pid, ppid=1, name=name, exe=exe, cmdline=cmdline, user=user,
        cpu_pct=cpu, mem_rss=1024, disk_bps=0.0, status=status, threads=1,
    )


@pytest.fixture
def snaps():
    other = "someoneelse" if ME != "someoneelse" else "anotheruser"
    return {
        1: _snap(1, ME, name="mine-a", cpu=5.0),
        2: _snap(2, ME, name="mine-b", cpu=90.0, exe="/usr/bin/mine-b"),
        3: _snap(3, other, name="sys-a", cpu=50.0),
        4: _snap(4, other, name="sys-b", cpu=1.0, cmdline="/sbin/sys-b --flag"),
    }, other


def _top_count(model) -> int:
    return model.rowCount(QModelIndex())


def test_grouped_has_two_group_headers(app, snaps):
    data, _ = snaps
    m = ProcessModel()
    m.update(data)
    assert _top_count(m) == 2  # User + System group headers
    user_idx = m.index(0, 0, QModelIndex())
    sys_idx = m.index(1, 0, QModelIndex())
    assert m.rowCount(user_idx) == 2  # two ME procs
    assert m.rowCount(sys_idx) == 2   # two other-user procs
    # Group header rows carry no snap.
    assert user_idx.internalPointer().snap is None


def test_flat_mode_flattens_to_one_level(app, snaps):
    data, _ = snaps
    m = ProcessModel()
    m.update(data)
    m.set_mode("flat")
    assert _top_count(m) == 4  # every process at the top level
    for r in range(_top_count(m)):
        node = m.index(r, 0, QModelIndex()).internalPointer()
        assert node.snap is not None  # no group headers in flat mode


def test_user_and_system_modes_filter_by_owner(app, snaps):
    data, _ = snaps
    m = ProcessModel()
    m.update(data)
    m.set_mode("user")
    assert _top_count(m) == 2
    assert all(m.index(r, 0, QModelIndex()).internalPointer().snap.user == ME
               for r in range(_top_count(m)))
    m.set_mode("system")
    assert _top_count(m) == 2
    assert all(m.index(r, 0, QModelIndex()).internalPointer().snap.user != ME
               for r in range(_top_count(m)))


def test_mode_round_trip_back_to_grouped(app, snaps):
    data, _ = snaps
    m = ProcessModel()
    m.update(data)
    m.set_mode("flat")
    m.set_mode("grouped")
    assert _top_count(m) == 2
    assert m.rowCount(m.index(0, 0, QModelIndex())) == 2


def test_proxy_needle_filters_top_level_leaves_in_flat_mode(app, snaps):
    data, _ = snaps
    m = ProcessModel()
    m.update(data)
    m.set_mode("flat")
    proxy = ProcessProxy()
    proxy.setSourceModel(m)
    proxy.set_needle("mine-b")
    visible = [proxy.index(r, 0, QModelIndex()).data() for r in range(proxy.rowCount(QModelIndex()))]
    assert visible == ["mine-b"]
