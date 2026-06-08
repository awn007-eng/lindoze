"""PMU parser + util-computation tests for the Intel backend.

These tests don't touch real hardware: we drive `IntelBackend._sample_util`
by feeding synthetic PMU counter pairs and a synthetic monotonic clock.
That lets us exercise the parser/arithmetic on machines without an Intel
GPU (e.g. Aaron's NVIDIA+AMD dev box).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lindoze import gpu_backends
from lindoze.gpu_backends import IntelBackend


def _make_backend_with_fake_pmu(
    counters_sequence: list[list[int]],
    wall_sequence: list[int],
) -> IntelBackend:
    """Construct an IntelBackend without running __init__ and wire up fake
    PMU state. counters_sequence is a list of per-engine counter snapshots
    (one snapshot = one call to sample). wall_sequence is the matching
    monotonic_ns values, length = len(counters_sequence)."""
    b = IntelBackend.__new__(IntelBackend)
    n_engines = len(counters_sequence[0])
    b._pmu_fds = list(range(n_engines))  # placeholder fds, never read
    b._pmu_prev_ns = list(counters_sequence[0])
    b._pmu_prev_wall = wall_sequence[0]
    b.hwmon = None
    b._freq_cur = None
    b._freq_max = None
    b.name = "fake"
    b.card_path = Path("/dev/null")
    b.device_path = Path("/dev/null")
    return b


@pytest.fixture
def patched_pmu(monkeypatch):
    """Patch `_read_pmu_counter` and `IntelBackend._monotonic_ns` to drive
    deterministic samples."""
    state = {"counters": [], "walls": [], "idx": 0}

    def fake_read(fd):
        # fd is the engine index. The "next sample" snapshot is at idx.
        return state["counters"][state["idx"]][fd]

    def fake_wall():
        return state["walls"][state["idx"]]

    monkeypatch.setattr(gpu_backends, "_read_pmu_counter", fake_read)
    monkeypatch.setattr(IntelBackend, "_monotonic_ns", staticmethod(fake_wall))
    return state


def test_sample_util_idle_returns_zero(patched_pmu):
    # Two engines, counters do not advance, 100ms elapses → 0% util.
    patched_pmu["counters"] = [[0, 0], [0, 0]]
    patched_pmu["walls"] = [0, 100_000_000]
    b = _make_backend_with_fake_pmu(patched_pmu["counters"], patched_pmu["walls"])
    patched_pmu["idx"] = 1
    assert b._sample_util() == 0


def test_sample_util_full_busy_returns_one_hundred(patched_pmu):
    # Each engine accrues exactly elapsed_ns of busy time = 100% per engine.
    elapsed = 100_000_000
    patched_pmu["counters"] = [[0, 0], [elapsed, elapsed]]
    patched_pmu["walls"] = [0, elapsed]
    b = _make_backend_with_fake_pmu(patched_pmu["counters"], patched_pmu["walls"])
    patched_pmu["idx"] = 1
    assert b._sample_util() == 100


def test_sample_util_half_busy(patched_pmu):
    # Both engines 50% busy: 50ms busy out of 100ms wall.
    elapsed = 100_000_000
    busy = elapsed // 2
    patched_pmu["counters"] = [[0, 0], [busy, busy]]
    patched_pmu["walls"] = [0, elapsed]
    b = _make_backend_with_fake_pmu(patched_pmu["counters"], patched_pmu["walls"])
    patched_pmu["idx"] = 1
    assert b._sample_util() == 50


def test_sample_util_averages_across_engines(patched_pmu):
    # One engine pegged, one idle → 50% overall (matches intel_gpu_top "overall").
    elapsed = 100_000_000
    patched_pmu["counters"] = [[0, 0], [elapsed, 0]]
    patched_pmu["walls"] = [0, elapsed]
    b = _make_backend_with_fake_pmu(patched_pmu["counters"], patched_pmu["walls"])
    patched_pmu["idx"] = 1
    assert b._sample_util() == 50


def test_sample_util_clamps_above_100(patched_pmu):
    # Pathological: counter delta exceeds elapsed (clock skew, kernel bug).
    elapsed = 100_000_000
    patched_pmu["counters"] = [[0], [elapsed * 3]]
    patched_pmu["walls"] = [0, elapsed]
    b = _make_backend_with_fake_pmu(patched_pmu["counters"], patched_pmu["walls"])
    patched_pmu["idx"] = 1
    assert b._sample_util() == 100


def test_sample_util_handles_counter_reset(patched_pmu):
    # Counter going backwards (reset/rollover) is treated as zero delta, not negative.
    elapsed = 100_000_000
    patched_pmu["counters"] = [[1_000_000_000], [42]]
    patched_pmu["walls"] = [0, elapsed]
    b = _make_backend_with_fake_pmu(patched_pmu["counters"], patched_pmu["walls"])
    patched_pmu["idx"] = 1
    assert b._sample_util() == 0


def test_sample_util_returns_none_when_no_fds():
    b = IntelBackend.__new__(IntelBackend)
    b._pmu_fds = []
    b._pmu_prev_ns = []
    b._pmu_prev_wall = None
    assert b._sample_util() is None


def test_sample_util_zero_elapsed_returns_none(patched_pmu):
    # Same monotonic-ns reading on both samples (extremely fast call).
    patched_pmu["counters"] = [[0], [0]]
    patched_pmu["walls"] = [1000, 1000]
    b = _make_backend_with_fake_pmu(patched_pmu["counters"], patched_pmu["walls"])
    patched_pmu["idx"] = 1
    assert b._sample_util() is None


# ---- pure parsers

def test_parse_event_config_simple(tmp_path):
    f = tmp_path / "rcs0-busy"
    f.write_text("event=0x0\n")
    assert IntelBackend._parse_event_config(f) == 0


def test_parse_event_config_with_extra_fields(tmp_path):
    f = tmp_path / "vcs0-busy"
    f.write_text("event=0x1000,umask=0x01\n")
    assert IntelBackend._parse_event_config(f) == 0x1000


def test_parse_event_config_handles_garbage(tmp_path):
    f = tmp_path / "bogus"
    f.write_text("not-a-config\n")
    assert IntelBackend._parse_event_config(f) is None


def test_parse_event_config_missing_file(tmp_path):
    assert IntelBackend._parse_event_config(tmp_path / "nope") is None


def test_pick_existing_first(tmp_path):
    a = tmp_path / "a"
    a.write_text("1")
    b = tmp_path / "b"
    b.write_text("2")
    assert IntelBackend._pick_existing(a, b) == a


def test_pick_existing_fallback(tmp_path):
    a = tmp_path / "missing"
    b = tmp_path / "present"
    b.write_text("ok")
    assert IntelBackend._pick_existing(a, b) == b


def test_pick_existing_all_missing(tmp_path):
    assert IntelBackend._pick_existing(tmp_path / "x", tmp_path / "y") is None
