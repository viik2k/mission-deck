"""Tests for HistoryStore uptime calculations using a temporary SQLite database."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mission_deck.history import HistoryStore, Sample
from mission_deck.models import DeviceStatus


@pytest.fixture
def store(tmp_path: Path) -> HistoryStore:
    s = HistoryStore.open(tmp_path / "test.db")
    yield s  # type: ignore[misc]
    s.close()


# --------------------------------------------------------------------------- #
# Empty-window edge cases
# --------------------------------------------------------------------------- #
def test_uptime_no_data_returns_none(store: HistoryStore) -> None:
    assert store.uptime("device-x") is None


def test_room_uptime_no_data_returns_none(store: HistoryStore) -> None:
    assert store.room_uptime("room-x") is None


# --------------------------------------------------------------------------- #
# 100% and 0% edge cases
# --------------------------------------------------------------------------- #
def test_uptime_all_online(store: HistoryStore) -> None:
    now = int(time.time())
    samples = [
        Sample("dev1", "room1", DeviceStatus.ONLINE, ts=now - i * 60)
        for i in range(4)
    ]
    store.record(samples)
    pct = store.uptime("dev1", since_seconds=86400)
    assert pct == 100.0


def test_uptime_all_offline(store: HistoryStore) -> None:
    now = int(time.time())
    samples = [
        Sample("dev1", "room1", DeviceStatus.OFFLINE, ts=now - i * 60)
        for i in range(4)
    ]
    store.record(samples)
    pct = store.uptime("dev1", since_seconds=86400)
    assert pct == 0.0


# --------------------------------------------------------------------------- #
# Mixed-status calculation
# --------------------------------------------------------------------------- #
def test_uptime_mixed_50_percent(store: HistoryStore) -> None:
    now = int(time.time())
    samples = [
        Sample("dev1", "room1", DeviceStatus.ONLINE, ts=now - 60),
        Sample("dev1", "room1", DeviceStatus.ONLINE, ts=now - 120),
        Sample("dev1", "room1", DeviceStatus.OFFLINE, ts=now - 180),
        Sample("dev1", "room1", DeviceStatus.OFFLINE, ts=now - 240),
    ]
    store.record(samples)
    pct = store.uptime("dev1", since_seconds=86400)
    assert pct == pytest.approx(50.0)


def test_uptime_75_percent(store: HistoryStore) -> None:
    now = int(time.time())
    samples = [
        Sample("dev1", "room1", DeviceStatus.ONLINE, ts=now - i * 60)
        for i in range(3)
    ] + [Sample("dev1", "room1", DeviceStatus.OFFLINE, ts=now - 300)]
    store.record(samples)
    pct = store.uptime("dev1", since_seconds=86400)
    assert pct == pytest.approx(75.0)


# --------------------------------------------------------------------------- #
# CHECKING / UNKNOWN samples must not be stored
# --------------------------------------------------------------------------- #
def test_checking_samples_excluded(store: HistoryStore) -> None:
    now = int(time.time())
    store.record([Sample("dev1", "room1", DeviceStatus.CHECKING, ts=now - 60)])
    store.record([Sample("dev1", "room1", DeviceStatus.UNKNOWN, ts=now - 120)])
    assert store.uptime("dev1") is None


# --------------------------------------------------------------------------- #
# Samples outside the window are ignored
# --------------------------------------------------------------------------- #
def test_samples_outside_window_ignored(store: HistoryStore) -> None:
    now = int(time.time())
    old = now - 90000  # 25 hours ago, outside the default 24h window
    store.record([Sample("dev1", "room1", DeviceStatus.OFFLINE, ts=old)])
    # No samples inside the window → None, not 0%
    assert store.uptime("dev1", since_seconds=86400) is None


# --------------------------------------------------------------------------- #
# Disabled store (conn=None) must not raise
# --------------------------------------------------------------------------- #
def test_disabled_store_uptime_returns_none(tmp_path: Path) -> None:
    s = HistoryStore(None, tmp_path / "nope.db")
    assert s.uptime("x") is None


def test_disabled_store_record_does_not_raise(tmp_path: Path) -> None:
    s = HistoryStore(None, tmp_path / "nope.db")
    s.record([Sample("x", "r", DeviceStatus.ONLINE)])  # must not raise


# --------------------------------------------------------------------------- #
# Room-level uptime aggregates across all devices in the room
# --------------------------------------------------------------------------- #
def test_room_uptime_aggregates_devices(store: HistoryStore) -> None:
    now = int(time.time())
    store.record([
        Sample("dev1", "room1", DeviceStatus.ONLINE, ts=now - 60),
        Sample("dev2", "room1", DeviceStatus.OFFLINE, ts=now - 60),
    ])
    pct = store.room_uptime("room1", since_seconds=86400)
    assert pct == pytest.approx(50.0)
