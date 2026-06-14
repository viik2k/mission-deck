"""The dashboard / overview homepage for mission-deck.

A single read-only screen that answers "how is the whole estate right now?"
without the operator having to click through every room. It is pure
presentation: it reads the live :class:`~mission_deck.models.Site` (whose device
statuses the estate sweep keeps current) and the persisted
:class:`~mission_deck.history.HistoryStore`, and calls back into :class:`App`
for the few actions it offers (refresh, toggle background polling, jump to a
room). It never touches the network or a worker thread itself.

Layout
------
A flat, full-width report — one column, reading top to bottom like an
instrument panel printout. Sections are separated by hairline rules rather
than boxed cards:

* **Stat strip** — headline counts (rooms, devices, online, offline, healthy
  rooms) as bare numbers, no tiles.
* **Needs attention** — every device currently offline, with a jump-to link.
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
# Cap how many rows the attention / recorders / uptime sections render. Past
# this an estate can have hundreds of entries; rendering them all builds
# hundreds of widgets and scrolls forever. We show the worst N and summarise
# the rest in a footer.
MAX_LIST_ROWS = 200
# Minimum seconds between navigate-triggered dashboard refreshes.
_REFRESH_THROTTLE_S = 2.0

# Hover corner for flat rows — just enough rounding to read as a highlight.
_ROW_CORNER = 6


# --------------------------------------------------------------------------- #
# Small building blocks
# --------------------------------------------------------------------------- #
class _Stat(ctk.CTkFrame):
    """One headline stat: a big mono value over an ALL CAPS caption. No box."""

    def __init__(self, master, caption: str):
        super().__init__(master, fg_color="transparent")
        self._value = ctk.CTkLabel(
            self, text="–", font=font(30, mono=True, weight="bold"),
            text_color=COLORS["text"],
        )
        self._value.pack(anchor="w")
        ctk.CTkLabel(
            self, text=caption.upper(),
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        ).pack(anchor="w")

    def set(self, value: str, accent: str | None = None) -> None:
        self._value.configure(text=value, text_color=accent or COLORS["text"])


def _rule(master) -> ctk.CTkFrame:
    """A 1px hairline divider."""

    return ctk.CTkFrame(master, height=1, corner_radius=0, fg_color=COLORS["border"])


class _SectionHeader(ctk.CTkFrame):
    """``▼ CAPTION ── count ───────────`` — collapseable section divider."""

    def __init__(self, master, text: str, tint: str | None = None):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(3, weight=1)
        self._collapsed = False
        self._on_toggle = None

        # A tinted chevron is a quiet colour landmark for the section, so the
        # flat report scans by hue (red = attention, amber = recorders, …).
        self._chevron = ctk.CTkLabel(
            self, text="▼", width=14,
            font=font(9, mono=True), text_color=tint or COLORS["text_faint"],
        )
        self._chevron.grid(row=0, column=0, sticky="w", padx=(0, 2))
        ctk.CTkLabel(
            self, text=text.upper(), anchor="w",
            font=font(10, mono=True, weight="bold"), text_color=COLORS["text_faint"],
        ).grid(row=0, column=1, sticky="w")
        self._count = ctk.CTkLabel(
            self, text="", anchor="w",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._count.grid(row=0, column=2, sticky="w", padx=(8, 0))
        _rule(self).grid(row=0, column=3, sticky="ew", padx=(GAP, 0))

        for widget in [self, *_descendants(self)]:
            widget.bind("<Button-1>", lambda _e: self._toggle())
            try:
                widget.configure(cursor="hand2")
            except tk.TclError:
                pass

    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._chevron.configure(text="▶" if self._collapsed else "▼")
        if self._on_toggle:
            self._on_toggle(self._collapsed)

    def bind_container(self, container: ctk.CTkFrame) -> None:
        self._on_toggle = lambda c: container.grid_remove() if c else container.grid()

    def set_count(self, text: str, color: str | None = None) -> None:
        self._count.configure(text=text, text_color=color or COLORS["text_faint"])


# --------------------------------------------------------------------------- #
# Dashboard view
# --------------------------------------------------------------------------- #
class DashboardView(ctk.CTkFrame):
    """The overview homepage. Built once; :meth:`refresh` repaints from state."""

    def __init__(self, master, app: "App"):  # noqa: F821 - App imported lazily by app.py
        super().__init__(master, corner_radius=0, fg_color=COLORS["panel"])
        self.app = app
        # Content signatures for the destroy-and-rebuild list sections, so a
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
        self._body = body
        row = 0

        # Stat strip — bare numbers separated by hairlines, no tiles.
        strip = ctk.CTkFrame(body, fg_color="transparent")
        strip.grid(row=row, column=0, sticky="ew", pady=(0, PAD))
        self._stats: dict[str, _Stat] = {}
        for index, (key, caption) in enumerate(
            (
                ("rooms", "Rooms"),
                ("devices", "Devices"),
                ("online", "Online"),
                ("offline", "Offline"),
                ("healthy", "Healthy rooms"),
            )
        ):
            if index:
                ctk.CTkFrame(
                    strip, width=1, corner_radius=0, fg_color=COLORS["border"],
                ).pack(side="left", fill="y", padx=PAD + 8, pady=4)
            stat = _Stat(strip, caption)
            stat.pack(side="left")
            self._stats[key] = stat
        row += 1

        _rule(body).grid(row=row, column=0, sticky="ew")
        row += 1

        # Flat sections, one under another, separated by their header rules.
        # Each header carries a colour landmark keyed to its meaning.
        self._hdr_attention = _SectionHeader(body, "Needs attention", COLORS["accent"])
        self._hdr_attention.grid(row=row, column=0, sticky="ew", pady=(PAD, 4))
        row += 1
        self._attention = self._section_container(body, row)
        self._hdr_attention.bind_container(self._attention)
        row += 1

        self._hdr_recorders = _SectionHeader(body, "Recorders", COLORS["warn"])
        self._hdr_recorders.grid(row=row, column=0, sticky="ew", pady=(PAD, 4))
        row += 1
        self._recorders = self._section_container(body, row)
        self._hdr_recorders.bind_container(self._recorders)
        row += 1

        self._hdr_uptime = _SectionHeader(body, "Uptime · last 24h", COLORS["accent2"])
        self._hdr_uptime.grid(row=row, column=0, sticky="ew", pady=(PAD, 4))
        row += 1
        self._uptime = self._section_container(body, row)
        self._hdr_uptime.bind_container(self._uptime)
        row += 1

        self._hdr_activity = _SectionHeader(body, "Recent activity")
        self._hdr_activity.grid(row=row, column=0, sticky="ew", pady=(PAD, 4))
        row += 1
        self._activity = self._section_container(body, row)
        self._hdr_activity.bind_container(self._activity)

    def _section_container(self, body, row: int) -> ctk.CTkFrame:
        container = ctk.CTkFrame(body, fg_color="transparent")
        container.grid(row=row, column=0, sticky="ew")
        container.grid_columnconfigure(0, weight=1)
        return container

    # ------------------------------------------------------------------ #
    # Refresh (UI thread only)
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Repaint every section from the current site/history state."""

        self._last_refresh_ts = time.monotonic()
        self._refresh_stats()
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

    def _refresh_stats(self) -> None:
        rooms = self.app.site.rooms
        devices = list(self.app.site.all_devices())
        online = sum(1 for d in devices if d.status is DeviceStatus.ONLINE)
        offline = sum(1 for d in devices if d.status is DeviceStatus.OFFLINE)
        healthy = sum(1 for r in rooms if r.devices and r.room_health is DeviceStatus.ONLINE)
        self._stats["rooms"].set(str(len(rooms)))
        self._stats["devices"].set(str(len(devices)))
        self._stats["online"].set(str(online), KPI_GOOD if online else KPI_NEUTRAL)
        self._stats["offline"].set(str(offline), KPI_BAD if offline else KPI_NEUTRAL)
        self._stats["healthy"].set(f"{healthy}/{len(rooms)}" if rooms else "0")

    def _refresh_attention(self) -> None:
        down: list[tuple[Room, Device]] = []
        for room in self.app.site.rooms:
            for device in room.devices:
                if device.status is DeviceStatus.OFFLINE:
                    down.append((room, device))
        self._hdr_attention.set_count(
            str(len(down)) if down else "0",
            COLORS["offline"] if down else None,
        )
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
            line = _row_button(self._attention, index)
            ctk.CTkLabel(
                line, text="●", width=16, font=font(13),
                text_color=status_color(DeviceStatus.OFFLINE),
            ).grid(row=0, column=0, padx=(8, 6))
            ctk.CTkLabel(
                line, text=device.name, anchor="w",
                font=font(12), text_color=COLORS["text"],
            ).grid(row=0, column=1, sticky="ew")
            ctk.CTkLabel(
                line, text=room.name.upper(), anchor="w",
                font=font(10, mono=True), text_color=COLORS["text_muted"],
            ).grid(row=0, column=2, sticky="w", padx=(GAP, 0))
            ctk.CTkLabel(
                line, text=device.address, anchor="e",
                font=font(10, mono=True), text_color=COLORS["text_faint"],
            ).grid(row=0, column=3, sticky="e", padx=(GAP, 10))
            _activate_row(line, lambda r=room: self.app.open_room_from_dashboard(r))
        if overflow > 0:
            _empty(self._attention, f"+ {overflow} more offline…")

    def _refresh_recorders(self) -> None:
        recorders = [
            (room, device)
            for room in self.app.site.rooms
            for device in room.devices
            if isinstance(device, Recorder)
        ]
        self._hdr_recorders.set_count(str(len(recorders)) if recorders else "")
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
            line = _row_button(self._recorders, index)
            ctk.CTkLabel(
                line, text=device.name, anchor="w",
                font=font(12, weight="bold"), text_color=COLORS["text"],
            ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
            ctk.CTkLabel(
                line, text=room.name.upper(), anchor="w",
                font=font(10, mono=True), text_color=COLORS["text_muted"],
            ).grid(row=0, column=2, sticky="w", padx=(GAP, 0))
            ctk.CTkLabel(
                line, text=recording_status_label(device.recording_status),
                font=font(11, weight="bold"),
                text_color=recording_status_color(device.recording_status),
            ).grid(row=0, column=3, sticky="e", padx=(GAP, 10))
            _activate_row(line, lambda r=room: self.app.open_room_from_dashboard(r))

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
            line = _row_button(self._uptime, index)
            ctk.CTkLabel(
                line, text=room.name, anchor="w",
                font=font(12), text_color=COLORS["text"],
            ).grid(row=0, column=1, sticky="ew", padx=(8, GAP))
            _UptimeBar(line, pct).grid(row=0, column=2, padx=(0, 8))
            ctk.CTkLabel(
                line, text="—" if pct is None else f"{pct:.0f}%", width=44, anchor="e",
                font=font(12, mono=True, weight="bold"),
                text_color=_uptime_color(pct),
            ).grid(row=0, column=3, padx=(0, 10))
            _activate_row(line, lambda r=room: self.app.open_room_from_dashboard(r))

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
            line.grid(row=index, column=0, sticky="ew", pady=1, padx=4)
            line.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                line, text=_event_summary(event), anchor="w",
                font=font(12), text_color=COLORS["text_muted"],
            ).grid(row=0, column=0, sticky="ew", padx=(8, 0))
            ctk.CTkLabel(
                line, text=_event_time(event), anchor="e",
                font=font(11, mono=True), text_color=COLORS["text_faint"],
            ).grid(row=0, column=1, padx=(GAP, 10))


# --------------------------------------------------------------------------- #
# Uptime bar (a tiny proportional reachability gauge)
# --------------------------------------------------------------------------- #
class _UptimeBar(tk.Canvas):
    """A slim horizontal bar: green = up portion, red = down, over the window."""

    WIDTH = 140
    HEIGHT = 8

    def __init__(self, master, pct: float | None):
        super().__init__(
            master, width=self.WIDTH, height=self.HEIGHT,
            highlightthickness=0, bd=0, bg=COLORS["panel"],
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
def _clear(frame: ctk.CTkFrame) -> None:
    for child in frame.winfo_children():
        child.destroy()


def _empty(frame: ctk.CTkFrame, text: str) -> None:
    ctk.CTkLabel(
        frame, text=text, anchor="w",
        font=font(12), text_color=COLORS["text_faint"],
    ).grid(row=999, column=0, sticky="ew", padx=8, pady=6)


def _row_button(frame: ctk.CTkFrame, index: int) -> ctk.CTkFrame:
    """A hover-highlighting, clickable row inside a flat section.

    Call :func:`_activate_row` once the row's children have been added so the
    hover/click bindings cover the labels too (Tk events don't bubble from
    child widgets to the row frame).
    """

    line = ctk.CTkFrame(frame, fg_color="transparent", corner_radius=_ROW_CORNER)
    line.grid(row=index, column=0, sticky="ew", pady=1)
    line.grid_columnconfigure(1, weight=1)
    return line


def _descendants(widget) -> list:
    found = []
    for child in widget.winfo_children():
        found.append(child)
        found.extend(_descendants(child))
    return found


def _activate_row(line: ctk.CTkFrame, command) -> None:
    """Bind hover highlight + click to a row and everything inside it."""

    def inside(widget) -> bool:
        w = widget
        while w is not None:
            if w is line:
                return True
            w = getattr(w, "master", None)
        return False

    def enter(_e=None):
        line.configure(fg_color=COLORS["card"])

    def leave(event):
        # Moving onto a child label fires <Leave> on the row; only clear the
        # highlight when the pointer has genuinely left the row's subtree.
        under = line.winfo_containing(event.x_root, event.y_root)
        if under is None or not inside(under):
            line.configure(fg_color="transparent")

    for widget in [line, *_descendants(line)]:
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)
        widget.bind("<Button-1>", lambda _e: command())
        try:
            widget.configure(cursor="hand2")
        except tk.TclError:
            pass


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
