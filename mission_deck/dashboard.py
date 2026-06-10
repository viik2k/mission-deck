"""The dashboard / overview homepage for mission-deck.

A single read-only screen that answers "how is the whole estate right now?"
without the operator having to click through every room. It is pure
presentation: it reads the live :class:`~mission_deck.models.Site` (whose device
statuses the estate sweep keeps current) and the persisted
:class:`~mission_deck.history.HistoryStore`, and calls back into :class:`App`
for the few actions it offers (refresh, toggle background polling, jump to a
room). It never touches the network or a worker thread itself.

Sections
--------
* **KPI bar** — headline counts (rooms, devices, online, offline, healthy rooms)
  plus the last-sweep time.
* **Attention list** — every device currently offline, with a jump-to link.
* **Recorders** — recording state of every recorder estate-wide (court-critical).
* **Uptime** — per-room reachability over the trailing 24h, worst first.
* **Activity** — the tail of the audit log (recent operator actions).
"""

from __future__ import annotations

import time
import tkinter as tk
from datetime import datetime

import customtkinter as ctk

from mission_deck.logging_setup import tail_audit
from mission_deck.models import (
    Device,
    DeviceStatus,
    Recorder,
    Room,
)
from mission_deck.theme import (
    COLORS,
    CORNER,
    GAP,
    KPI_BAD,
    KPI_GOOD,
    KPI_NEUTRAL,
    PAD,
    recording_status_color,
    recording_status_label,
    status_color,
)
from mission_deck.ui import font

# Trailing window the uptime panel summarises.
UPTIME_WINDOW_SECONDS = 24 * 3600
# Activity feed length.
ACTIVITY_LIMIT = 18
# Cap how many rows the attention / recorders lists render. Past this an estate
# can have hundreds of entries; rendering them all builds hundreds of widgets and
# scrolls forever. We show the worst N and summarise the rest in a footer.
MAX_LIST_ROWS = 200
# Minimum seconds between navigate-triggered dashboard refreshes.
_REFRESH_THROTTLE_S = 2.0


# --------------------------------------------------------------------------- #
# Small building blocks
# --------------------------------------------------------------------------- #
class StatTile(ctk.CTkFrame):
    """One KPI tile: a big mono value over an ALL CAPS caption."""

    def __init__(self, master, caption: str):
        super().__init__(
            master, corner_radius=CORNER, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        self._value = ctk.CTkLabel(
            self, text="–", font=font(32, mono=True, weight="bold"),
            text_color=COLORS["text"],
        )
        self._value.pack(anchor="w", padx=PAD, pady=(PAD, 0))
        self._caption = ctk.CTkLabel(
            self, text=caption.upper(),
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._caption.pack(anchor="w", padx=PAD, pady=(0, PAD - 4))

    def set(self, value: str, accent: str | None = None) -> None:
        self._value.configure(text=value, text_color=accent or COLORS["text"])


def _section_label(master, text: str) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        master, text=text.upper(), anchor="w",
        font=font(10, mono=True, weight="bold"), text_color=COLORS["text_faint"],
    )


# --------------------------------------------------------------------------- #
# Dashboard view
# --------------------------------------------------------------------------- #
class DashboardView(ctk.CTkFrame):
    """The overview homepage. Built once; :meth:`refresh` repaints from state."""

    def __init__(self, master, app: "App"):  # noqa: F821 - App imported lazily by app.py
        super().__init__(master, corner_radius=0, fg_color=COLORS["panel"])
        self.app = app
        # Content signatures for the destroy-and-rebuild list panels, so a
        # refresh whose data hasn't changed (e.g. a sweep that flipped nothing)
        # skips rebuilding hundreds of row widgets. ``None`` forces a first build.
        self._sig_attention: tuple | None = None
        self._sig_recorders: tuple | None = None
        self._sig_uptime: tuple | None = None
        self._sig_activity: tuple | None = None
        self._last_refresh_ts: float = 0.0

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # The shared top bar (in App) owns the title, Refresh All / Auto controls
        # and the user chip for this view; the dashboard only renders its body.
        self._build_body()
        self.refresh()

    # ------------------------------------------------------------------ #
    # Body
    # ------------------------------------------------------------------ #
    def _build_body(self) -> None:
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=PAD)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        self._body = body
        row = 0

        # KPI bar (spans both columns).
        self._kpi_bar = ctk.CTkFrame(body, fg_color="transparent")
        self._kpi_bar.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, GAP))
        self._kpi_tiles: dict[str, StatTile] = {}
        for index, (key, caption) in enumerate(
            (
                ("rooms", "Rooms"),
                ("devices", "Devices"),
                ("online", "Online"),
                ("offline", "Offline"),
                ("healthy", "Healthy rooms"),
            )
        ):
            self._kpi_bar.grid_columnconfigure(index, weight=1, uniform="kpi")
            tile = StatTile(self._kpi_bar, caption)
            tile.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else GAP, 0))
            self._kpi_tiles[key] = tile
        row += 1

        # Lower panels in two columns: left = Attention + Recorders,
        # right = Uptime + Activity.
        _section_label(body, "Needs attention").grid(row=row, column=0, sticky="w", pady=(GAP, 6), padx=(0, GAP // 2))
        _section_label(body, "Uptime · last 24h").grid(row=row, column=1, sticky="w", pady=(GAP, 6), padx=(GAP // 2, 0))
        row += 1
        self._attention = ctk.CTkScrollableFrame(body, fg_color=COLORS["card"], corner_radius=CORNER, height=200)
        self._attention.grid(row=row, column=0, sticky="nsew", padx=(0, GAP // 2), pady=(0, GAP))
        self._attention.grid_columnconfigure(0, weight=1)
        self._uptime = ctk.CTkScrollableFrame(body, fg_color=COLORS["card"], corner_radius=CORNER, height=200)
        self._uptime.grid(row=row, column=1, sticky="nsew", padx=(GAP // 2, 0), pady=(0, GAP))
        self._uptime.grid_columnconfigure(0, weight=1)
        row += 1

        _section_label(body, "Recorders").grid(row=row, column=0, sticky="w", pady=(GAP, 6), padx=(0, GAP // 2))
        _section_label(body, "Recent activity").grid(row=row, column=1, sticky="w", pady=(GAP, 6), padx=(GAP // 2, 0))
        row += 1
        self._recorders = ctk.CTkScrollableFrame(body, fg_color=COLORS["card"], corner_radius=CORNER, height=200)
        self._recorders.grid(row=row, column=0, sticky="nsew", padx=(0, GAP // 2))
        self._recorders.grid_columnconfigure(0, weight=1)
        self._activity = ctk.CTkScrollableFrame(body, fg_color=COLORS["card"], corner_radius=CORNER, height=200)
        self._activity.grid(row=row, column=1, sticky="nsew", padx=(GAP // 2, 0))
        self._activity.grid_columnconfigure(0, weight=1)

    # ------------------------------------------------------------------ #
    # Refresh (UI thread only)
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Repaint every section from the current site/history state."""

        self._last_refresh_ts = time.monotonic()
        self._refresh_kpis()
        self._refresh_attention()
        self._refresh_recorders()
        self._refresh_uptime()
        self._refresh_activity()

    def refresh_if_stale(self) -> None:
        """Refresh only when _REFRESH_THROTTLE_S seconds have elapsed since the last full refresh.

        Called on every navigate("overview") so rapid tab-switching doesn't
        repeatedly re-scan all rooms/devices. Post-sweep paths call refresh() directly.
        """

        if time.monotonic() - self._last_refresh_ts >= _REFRESH_THROTTLE_S:
            self.refresh()

    def set_sweeping(self, sweeping: bool) -> None:
        """Reflect an in-progress estate sweep — delegated to the shared top bar."""

        self.app._set_overview_sweeping(sweeping)

    def _refresh_kpis(self) -> None:
        rooms = self.app.site.rooms
        devices = list(self.app.site.all_devices())
        online = sum(1 for d in devices if d.status is DeviceStatus.ONLINE)
        offline = sum(1 for d in devices if d.status is DeviceStatus.OFFLINE)
        healthy = sum(1 for r in rooms if r.devices and r.room_health is DeviceStatus.ONLINE)
        self._kpi_tiles["rooms"].set(str(len(rooms)))
        self._kpi_tiles["devices"].set(str(len(devices)))
        self._kpi_tiles["online"].set(str(online), KPI_GOOD if online else KPI_NEUTRAL)
        self._kpi_tiles["offline"].set(str(offline), KPI_BAD if offline else KPI_NEUTRAL)
        self._kpi_tiles["healthy"].set(f"{healthy}/{len(rooms)}" if rooms else "0")

    def _refresh_attention(self) -> None:
        down: list[tuple[Room, Device]] = []
        for room in self.app.site.rooms:
            for device in room.devices:
                if device.status is DeviceStatus.OFFLINE:
                    down.append((room, device))
        signature = tuple(device.id for _, device in down)
        if signature == self._sig_attention:
            return  # same offline set as last paint — nothing to redo
        self._sig_attention = signature

        _clear(self._attention)
        if not down:
            _empty(self._attention, "All checked devices are online.")
            return
        overflow = len(down) - MAX_LIST_ROWS
        for index, (room, device) in enumerate(down[:MAX_LIST_ROWS]):
            line = _row_button(
                self._attention, index,
                command=lambda r=room: self.app.open_room_from_dashboard(r),
            )
            ctk.CTkLabel(
                line, text="●", width=16, font=font(13),
                text_color=status_color(DeviceStatus.OFFLINE),
            ).grid(row=0, column=0, padx=(8, 6))
            text = ctk.CTkFrame(line, fg_color="transparent")
            text.grid(row=0, column=1, sticky="ew")
            text.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                text, text=device.name, anchor="w",
                font=font(12), text_color=COLORS["text"],
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkLabel(
                text, text=f"{room.name.upper()}   ·   {device.address}", anchor="w",
                font=font(10, mono=True), text_color=COLORS["text_faint"],
            ).grid(row=1, column=0, sticky="ew")
        if overflow > 0:
            _empty(self._attention, f"+ {overflow} more offline…")

    def _refresh_recorders(self) -> None:
        recorders = [
            (room, device)
            for room in self.app.site.rooms
            for device in room.devices
            if isinstance(device, Recorder)
        ]
        signature = tuple(
            (device.id, device.recording_status) for _, device in recorders
        )
        if signature == self._sig_recorders:
            return
        self._sig_recorders = signature

        _clear(self._recorders)
        if not recorders:
            _empty(self._recorders, "No recorders configured.")
            return
        for index, (room, device) in enumerate(recorders[:MAX_LIST_ROWS]):
            line = _row_button(
                self._recorders, index,
                command=lambda r=room: self.app.open_room_from_dashboard(r),
            )
            text = ctk.CTkFrame(line, fg_color="transparent")
            text.grid(row=0, column=0, sticky="ew", padx=(8, 0))
            text.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                text, text=device.name, anchor="w",
                font=font(12, weight="bold"), text_color=COLORS["text"],
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkLabel(
                text, text=room.name, anchor="w",
                font=font(11), text_color=COLORS["text_faint"],
            ).grid(row=1, column=0, sticky="ew")
            ctk.CTkLabel(
                line, text=recording_status_label(device.recording_status),
                font=font(11, weight="bold"),
                text_color=recording_status_color(device.recording_status),
            ).grid(row=0, column=1, padx=(GAP, 10))

    def _refresh_uptime(self) -> None:
        history = self.app.history
        rooms = [r for r in self.app.site.rooms if r.devices]
        # One grouped query for every room, not one query per room.
        uptimes = history.rooms_uptime([r.id for r in rooms], UPTIME_WINDOW_SECONDS)
        rows: list[tuple[Room, float | None]] = [
            (room, uptimes.get(room.id)) for room in rooms
        ]
        # Worst (and unknown) first so problems surface at the top.
        rows.sort(key=lambda rp: (rp[1] is not None, rp[1] if rp[1] is not None else 0.0))
        rows = rows[:MAX_LIST_ROWS]
        signature = tuple(
            (room.id, None if pct is None else round(pct, 1)) for room, pct in rows
        )
        if signature == self._sig_uptime:
            return
        self._sig_uptime = signature

        _clear(self._uptime)
        if not any(pct is not None for _, pct in rows):
            _empty(self._uptime, "No uptime history yet — run a sweep.")
            return
        for index, (room, pct) in enumerate(rows):
            line = _row_button(
                self._uptime, index,
                command=lambda r=room: self.app.open_room_from_dashboard(r),
            )
            ctk.CTkLabel(
                line, text=room.name, anchor="w",
                font=font(12), text_color=COLORS["text"],
            ).grid(row=0, column=0, sticky="ew", padx=(8, GAP))
            _UptimeBar(line, pct).grid(row=0, column=1, padx=(0, 8))
            ctk.CTkLabel(
                line, text="—" if pct is None else f"{pct:.0f}%", width=44, anchor="e",
                font=font(12, weight="bold"),
                text_color=_uptime_color(pct),
            ).grid(row=0, column=2, padx=(0, 10))
            line.grid_columnconfigure(0, weight=1)

    def _refresh_activity(self) -> None:
        events = tail_audit(ACTIVITY_LIMIT)
        signature = tuple(
            (e.get("ts"), e.get("event"), e.get("user")) for e in events
        )
        if signature == self._sig_activity:
            return
        self._sig_activity = signature

        _clear(self._activity)
        if not events:
            _empty(self._activity, "No recent activity recorded.")
            return
        for index, event in enumerate(events):
            line = ctk.CTkFrame(self._activity, fg_color="transparent")
            line.grid(row=index, column=0, sticky="ew", pady=2, padx=4)
            line.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                line, text=_event_summary(event), anchor="w",
                font=font(12), text_color=COLORS["text"],
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkLabel(
                line, text=_event_time(event), anchor="e",
                font=font(11, mono=True), text_color=COLORS["text_faint"],
            ).grid(row=0, column=1, padx=(GAP, 0))


# --------------------------------------------------------------------------- #
# Uptime bar (a tiny proportional reachability gauge)
# --------------------------------------------------------------------------- #
class _UptimeBar(tk.Canvas):
    """A slim horizontal bar: green = up portion, red = down, over the window."""

    WIDTH = 120
    HEIGHT = 12

    def __init__(self, master, pct: float | None):
        super().__init__(
            master, width=self.WIDTH, height=self.HEIGHT,
            highlightthickness=0, bd=0, bg=COLORS["card"],
        )
        self._draw(pct)

    def _draw(self, pct: float | None) -> None:
        self.delete("all")
        if pct is None:
            self.create_rectangle(0, 0, self.WIDTH, self.HEIGHT, fill=COLORS["border"], width=0)
            return
        up_w = int(self.WIDTH * max(0.0, min(100.0, pct)) / 100.0)
        # Down portion underneath, then the up portion on top from the left.
        self.create_rectangle(0, 0, self.WIDTH, self.HEIGHT, fill=COLORS["offline"], width=0)
        if up_w > 0:
            self.create_rectangle(0, 0, up_w, self.HEIGHT, fill=COLORS["online"], width=0)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _clear(frame: ctk.CTkScrollableFrame) -> None:
    for child in frame.winfo_children():
        child.destroy()


def _empty(frame: ctk.CTkScrollableFrame, text: str) -> None:
    ctk.CTkLabel(
        frame, text=text, anchor="w",
        font=font(12), text_color=COLORS["text_muted"],
    ).grid(row=0, column=0, sticky="ew", padx=10, pady=10)


def _row_button(frame: ctk.CTkScrollableFrame, index: int, command) -> ctk.CTkFrame:
    """A hover-highlighting, clickable row inside a scrollable list."""

    line = ctk.CTkFrame(frame, fg_color="transparent", corner_radius=CORNER)
    line.grid(row=index, column=0, sticky="ew", pady=2, padx=4)
    line.grid_columnconfigure(1, weight=1)

    def enter(_e=None):
        line.configure(fg_color=COLORS["card_hover"])

    def leave(_e=None):
        line.configure(fg_color="transparent")

    line.bind("<Enter>", enter)
    line.bind("<Leave>", leave)
    line.bind("<Button-1>", lambda _e: command())
    line.configure(cursor="hand2")
    return line


def _uptime_color(pct: float | None) -> str:
    if pct is None:
        return COLORS["text_faint"]
    if pct >= 99.0:
        return COLORS["online"]
    if pct >= 90.0:
        return COLORS["warn"]
    return COLORS["offline"]


def _event_summary(event: dict) -> str:
    name = str(event.get("event", "event"))
    detail = (
        event.get("room_name")
        or event.get("device_name")
        or event.get("control")
        or event.get("path")
        or ""
    )
    user = event.get("user")
    parts = [name]
    if detail:
        parts.append(str(detail))
    line = "  ·  ".join(parts)
    return f"{line}   ({user})" if user else line


def _event_time(event: dict) -> str:
    raw = event.get("ts")
    if not isinstance(raw, str):
        return ""
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return raw[:19]
    return when.astimezone().strftime("%m-%d %H:%M")
