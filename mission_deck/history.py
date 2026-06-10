"""Persisted uptime history for mission-deck devices.

Every status sweep (the dashboard's estate-wide poll, and each per-room check)
appends one :class:`Sample` per resolved device here. That turns the app's
otherwise-ephemeral green/red dots into a record we can ask questions of:

  * "what % of the last 24h was this device reachable?" (:meth:`uptime`)
  * "how has this room trended?" (:meth:`room_uptime`)
  * "draw me a sparkline of the last N checks" (:meth:`device_series`)

Design constraints (mirroring ``state.py`` and the audit log):

* **Stdlib only** — a single SQLite database via :mod:`sqlite3`.
* **Best-effort** — a missing/locked/corrupt database must never break the app.
  Every public method swallows :class:`sqlite3.Error` (logging a warning) and
  returns a benign default, exactly like a failed preference write.
* **Thread-safe** — one connection shared across the Tk thread (reads) and the
  background sweep worker (writes), opened ``check_same_thread=False`` and
  serialised by a lock. WAL mode keeps readers and the writer from blocking.

The database lives in the writable per-user directory (so a read-only packaged
EXE still works); override the location with ``MISSION_DECK_HISTORY_DB``.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from mission_deck.config import user_config_dir
from mission_deck.models import DeviceStatus

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "history.db"
DEFAULT_RETENTION_DAYS = 30

# Only resolved states are worth persisting; CHECKING/UNKNOWN carry no uptime
# signal and would just dilute the percentages.
_PERSISTED = (DeviceStatus.ONLINE, DeviceStatus.OFFLINE)


def history_path() -> Path:
    """Location of the history database (``MISSION_DECK_HISTORY_DB`` overrides)."""

    override = os.environ.get("MISSION_DECK_HISTORY_DB")
    if override:
        return Path(override).expanduser()
    return user_config_dir() / HISTORY_FILENAME


@dataclass(slots=True)
class Sample:
    """One reachability observation for a device at a point in time."""

    device_id: str
    room_id: str
    status: DeviceStatus
    latency_ms: float | None = None
    ts: int | None = None  # epoch seconds; filled in at record time if None


class HistoryStore:
    """A best-effort SQLite store of device reachability samples over time."""

    def __init__(self, conn: sqlite3.Connection | None, path: Path) -> None:
        self._conn = conn
        self._path = path
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    @classmethod
    def open(cls, path: Path | None = None) -> "HistoryStore":
        """Open (creating if needed) the history database.

        Never raises: on any failure the returned store is *disabled* (a no-op
        that records nothing and reports empty history), so the app runs fine
        even when the disk is read-only or the file is corrupt.
        """

        target = path or history_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(target), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS samples (
                    ts         INTEGER NOT NULL,
                    device_id  TEXT    NOT NULL,
                    room_id    TEXT    NOT NULL,
                    status     TEXT    NOT NULL,
                    latency_ms REAL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_samples_device_ts "
                "ON samples (device_id, ts)"
            )
            # Supports the dashboard's single grouped per-room uptime query
            # (rooms_uptime) without a full-table scan at estate scale.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_samples_room_ts "
                "ON samples (room_id, ts)"
            )
            conn.commit()
        except sqlite3.Error as exc:
            logger.warning("Could not open history database %s: %s", target, exc)
            return cls(None, target)
        logger.info("Uptime history database ready at %s", target)
        return cls(conn, target)

    @property
    def enabled(self) -> bool:
        """True when a database connection is available (open succeeded)."""

        return self._conn is not None

    def close(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error as exc:
                logger.debug("Error closing history database: %s", exc)
            finally:
                self._conn = None

    # ------------------------------------------------------------------ #
    # Writing
    # ------------------------------------------------------------------ #
    def record(self, samples: list[Sample]) -> None:
        """Append a batch of samples (one sweep). Best-effort; never raises.

        Only resolved (ONLINE/OFFLINE) samples are stored — see :data:`_PERSISTED`.
        Safe to call from the background sweep worker thread.
        """

        if self._conn is None or not samples:
            return
        now = int(time.time())
        rows = [
            (s.ts or now, s.device_id, s.room_id, s.status.value, s.latency_ms)
            for s in samples
            if s.status in _PERSISTED
        ]
        if not rows:
            return
        with self._lock:
            try:
                self._conn.executemany(
                    "INSERT INTO samples (ts, device_id, room_id, status, latency_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
                self._conn.commit()
            except sqlite3.Error as exc:
                logger.warning("Could not record %d history sample(s): %s", len(rows), exc)

    def prune(self, older_than_days: int = DEFAULT_RETENTION_DAYS) -> None:
        """Delete samples older than ``older_than_days``. Best-effort."""

        if self._conn is None or older_than_days <= 0:
            return
        cutoff = int(time.time()) - older_than_days * 86400
        with self._lock:
            try:
                self._conn.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
                self._conn.commit()
            except sqlite3.Error as exc:
                logger.warning("Could not prune history: %s", exc)

    # ------------------------------------------------------------------ #
    # Reading
    # ------------------------------------------------------------------ #
    def uptime(self, device_id: str, since_seconds: int = 86400) -> float | None:
        """Online percentage for one device over the trailing window.

        Returns a value in 0..100, or ``None`` when there is no data in the
        window (so callers can show "—" rather than a misleading 0%).
        """

        return self._uptime_where("device_id = ?", (device_id,), since_seconds)

    def room_uptime(self, room_id: str, since_seconds: int = 86400) -> float | None:
        """Online percentage across every device in a room over the window."""

        return self._uptime_where("room_id = ?", (room_id,), since_seconds)

    def rooms_uptime(
        self, room_ids: list[str], since_seconds: int = 86400
    ) -> dict[str, float | None]:
        """Per-room online percentage for many rooms in a single query.

        The dashboard needs every room's uptime at once; issuing one query per
        room is N round-trips against the database on every refresh. This does
        it as one grouped scan and returns ``{room_id: pct-or-None}`` with every
        requested room present (``None`` when it has no samples in the window).
        """

        result: dict[str, float | None] = {rid: None for rid in room_ids}
        if self._conn is None or not room_ids:
            return result
        cutoff = int(time.time()) - max(0, since_seconds)
        wanted = set(room_ids)
        with self._lock:
            try:
                cur = self._conn.execute(
                    "SELECT room_id, "
                    "SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), COUNT(*) "
                    "FROM samples WHERE ts >= ? GROUP BY room_id",
                    (DeviceStatus.ONLINE.value, cutoff),
                )
                rows = cur.fetchall()
            except sqlite3.Error as exc:
                logger.warning("Could not read rooms uptime: %s", exc)
                return result
        for room_id, online, total in rows:
            if room_id in wanted and total:
                result[room_id] = (online or 0) * 100.0 / total
        return result

    def devices_uptime(
        self, device_ids: list[str], since_seconds: int = 86400
    ) -> dict[str, float | None]:
        """Per-device online percentage for many devices in a single query.

        The status-report export needs every device's uptime at once; like
        :meth:`rooms_uptime` this does one grouped scan and returns
        ``{device_id: pct-or-None}`` with every requested device present
        (``None`` when it has no samples in the window).
        """

        result: dict[str, float | None] = {did: None for did in device_ids}
        if self._conn is None or not device_ids:
            return result
        cutoff = int(time.time()) - max(0, since_seconds)
        wanted = set(device_ids)
        with self._lock:
            try:
                cur = self._conn.execute(
                    "SELECT device_id, "
                    "SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), COUNT(*) "
                    "FROM samples WHERE ts >= ? GROUP BY device_id",
                    (DeviceStatus.ONLINE.value, cutoff),
                )
                rows = cur.fetchall()
            except sqlite3.Error as exc:
                logger.warning("Could not read devices uptime: %s", exc)
                return result
        for device_id, online, total in rows:
            if device_id in wanted and total:
                result[device_id] = (online or 0) * 100.0 / total
        return result

    def _uptime_where(
        self, where: str, params: tuple, since_seconds: int
    ) -> float | None:
        if self._conn is None:
            return None
        cutoff = int(time.time()) - max(0, since_seconds)
        with self._lock:
            try:
                cur = self._conn.execute(
                    f"SELECT "
                    f"SUM(CASE WHEN status = ? THEN 1 ELSE 0 END), COUNT(*) "
                    f"FROM samples WHERE {where} AND ts >= ?",
                    (DeviceStatus.ONLINE.value, *params, cutoff),
                )
                online, total = cur.fetchone()
            except sqlite3.Error as exc:
                logger.warning("Could not read uptime: %s", exc)
                return None
        if not total:
            return None
        return (online or 0) * 100.0 / total

    def device_series(
        self, device_id: str, since_seconds: int = 86400, limit: int = 240
    ) -> list[tuple[int, DeviceStatus]]:
        """Recent ``(ts, status)`` samples for a device, oldest-first.

        Capped at ``limit`` most-recent points — enough to paint a sparkline
        without dragging the whole window into memory.
        """

        if self._conn is None:
            return []
        cutoff = int(time.time()) - max(0, since_seconds)
        with self._lock:
            try:
                cur = self._conn.execute(
                    "SELECT ts, status FROM samples "
                    "WHERE device_id = ? AND ts >= ? ORDER BY ts DESC LIMIT ?",
                    (device_id, cutoff, limit),
                )
                rows = cur.fetchall()
            except sqlite3.Error as exc:
                logger.warning("Could not read device series: %s", exc)
                return []
        series: list[tuple[int, DeviceStatus]] = []
        for ts, status in reversed(rows):  # oldest-first for left-to-right plots
            try:
                series.append((int(ts), DeviceStatus(status)))
            except ValueError:
                continue  # unknown status string from a future schema; skip
        return series
