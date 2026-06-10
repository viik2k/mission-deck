"""Estate status report export for mission-deck.

Turns the live :class:`~mission_deck.models.Site` (plus the persisted uptime
history) into a flat CSV an AV tech can hand to court administration: one row
per device with its room, address, live status, last latency and trailing-24h
uptime. Pure data-out — no GUI, no networking; the UI layer picks the path and
runs :func:`write_csv` off the Tk thread.
"""

from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path

from mission_deck.history import HistoryStore
from mission_deck.models import DeviceStatus, Recorder, Site
from mission_deck.theme import status_label

logger = logging.getLogger(__name__)

# Trailing window the uptime column summarises (matches the dashboard panel).
REPORT_UPTIME_WINDOW_SECONDS = 24 * 3600

CSV_COLUMNS = [
    "city",
    "room",
    "device",
    "type",
    "manufacturer",
    "model",
    "address",
    "status",
    "latency_ms",
    "uptime_24h_pct",
    "recording",
    "last_error",
]


def estate_rows(
    site: Site,
    history: HistoryStore,
    window_seconds: int = REPORT_UPTIME_WINDOW_SECONDS,
) -> list[dict[str, str]]:
    """One CSV-ready row (all strings, keyed by :data:`CSV_COLUMNS`) per device."""

    uptimes = history.devices_uptime(
        [device.id for device in site.all_devices()], window_seconds
    )
    rows: list[dict[str, str]] = []
    for room in site.rooms:
        for device in room.devices:
            pct = uptimes.get(device.id)
            rows.append(
                {
                    "city": room.city,
                    "room": room.name,
                    "device": device.name,
                    "type": device.category,
                    "manufacturer": device.manufacturer,
                    "model": device.model,
                    "address": device.address,
                    "status": status_label(device.status),
                    "latency_ms": (
                        f"{device.last_latency_ms:.0f}"
                        if device.status is DeviceStatus.ONLINE
                        and device.last_latency_ms is not None
                        else ""
                    ),
                    "uptime_24h_pct": "" if pct is None else f"{pct:.1f}",
                    "recording": (
                        device.recording_status.value
                        if isinstance(device, Recorder)
                        else ""
                    ),
                    "last_error": device.last_error or "",
                }
            )
    return rows


def write_csv(
    path: Path | str,
    site: Site,
    history: HistoryStore,
    window_seconds: int = REPORT_UPTIME_WINDOW_SECONDS,
) -> int:
    """Write the estate report to ``path``; returns the number of device rows.

    ``utf-8-sig`` so Excel opens it with correct encoding detection;
    ``newline=""`` per the :mod:`csv` module contract.
    """

    rows = estate_rows(site, history, window_seconds)
    target = Path(path)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Exported status report (%d devices) to %s", len(rows), target)
    return len(rows)


def default_filename(now: datetime | None = None) -> str:
    """A timestamped default name for the save dialog."""

    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"mission-deck-status-{stamp}.csv"
