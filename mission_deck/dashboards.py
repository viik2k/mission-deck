"""Composable dashboards for mission-deck.

The operator builds their own monitoring screen from a catalogue of widgets
bound to live estate data: KPI tiles, a radial health gauge, an estate-pulse
wall (one status cell per device), offline/recorder/latency lists, a
device-category mix, per-room uptime, and 24-hour uptime/latency trends drawn
from the persisted history store. The
layout (an ordered list of widget ids) persists per-user in ``state.json``
(``AppState.dashboard_widgets``) so every operator can keep the board that
matches their duty — a recording-compliance wall, a network-latency view, …

Pure presentation, mirroring ``dashboard.py``'s constraints: widgets read the
live :class:`Site` and :class:`HistoryStore` and call back into :class:`App`
to navigate; nothing here touches the network or a worker thread. Rendering is
a full rebuild — boards are a dozen-odd widgets, and they only repaint when a
sweep/check completes or the layout changes, so pooling would be complexity
without a payoff.
"""

from __future__ import annotations

import math
import time
import tkinter as tk
from dataclasses import dataclass

import customtkinter as ctk

from mission_deck.icons import icon
from mission_deck.logging_setup import tail_audit
from mission_deck.models import DeviceStatus, Recorder, RecordingStatus
from mission_deck.theme import (
    COLORS,
    CORNER,
    CORNER_LG,
    FONT_MONO,
    GAP,
    KPI_BAD,
    KPI_GOOD,
    KPI_NEUTRAL,
    PAD,
    STATUS_COLORS,
    recording_status_color,
    recording_status_label,
)
from mission_deck.ui import BTN_GHOST, BTN_SOLID, font

# Board geometry: widgets flow into a fixed column grid, spanning 1..GRID columns.
GRID = 4
# Trailing window + resolution for the trend widgets.
TREND_WINDOW_S = 24 * 3600
TREND_BUCKETS = 24
# Row caps so a huge estate can't turn one widget into an endless column.
MAX_ROWS = 8
# Minimum seconds between navigate-triggered refreshes (matches dashboard.py).
_REFRESH_THROTTLE_S = 2.0


@dataclass(frozen=True)
class WidgetSpec:
    """Catalogue entry: identity, display metadata and grid span."""

    id: str
    name: str
    desc: str
    icon: str
    span: int = 1


WIDGETS: list[WidgetSpec] = [
    WidgetSpec("kpi_online", "Online devices", "Devices currently reachable.", "check"),
    WidgetSpec("kpi_offline", "Offline devices", "Devices currently unreachable.", "warn"),
    WidgetSpec("kpi_devices", "Device count", "Everything under monitoring.", "server"),
    WidgetSpec("kpi_rooms", "Healthy rooms", "Rooms with every device online.", "rooms"),
    WidgetSpec("kpi_latency", "Average latency", "Mean probe latency right now.", "pulse"),
    WidgetSpec("kpi_recording", "Recording now", "Recorders currently capturing.", "record"),
    WidgetSpec("kpi_uptime", "Estate uptime · 24h", "Average reachability over the day.", "clock"),
    WidgetSpec("health_gauge", "Health gauge", "Live reachability as a radial gauge.", "pulse"),
    WidgetSpec("status_grid", "Estate pulse", "Every device as a status cell — one wall.", "matrix", span=2),
    WidgetSpec("uptime_trend", "Uptime trend · 24h", "Estate reachability per hour.", "check", span=2),
    WidgetSpec("latency_trend", "Latency trend · 24h", "Average probe latency per hour.", "pulse", span=2),
    WidgetSpec("offline_list", "Needs attention", "Offline devices, worst rooms first.", "warn", span=2),
    WidgetSpec("slowest_devices", "Slowest devices", "Highest live latency, worst first.", "bolt", span=2),
    WidgetSpec("device_mix", "Device mix", "Estate composition by device category.", "server", span=2),
    WidgetSpec("recorders", "Recorders", "Recording state across the estate.", "record", span=2),
    WidgetSpec("room_uptime", "Room uptime · 24h", "Worst-performing rooms first.", "rooms", span=2),
    WidgetSpec("activity", "Recent activity", "Latest operator actions (audit log).", "clock", span=2),
]

# The board a user sees before they customise anything.
DEFAULT_LAYOUT: list[str] = [
    "health_gauge", "kpi_uptime", "kpi_offline", "kpi_recording",
    "status_grid", "device_mix",
    "uptime_trend", "latency_trend",
    "offline_list", "slowest_devices",
]


def widget_by_id(widget_id: str) -> WidgetSpec | None:
    for spec in WIDGETS:
        if spec.id == widget_id:
            return spec
    return None


# --------------------------------------------------------------------------- #
# Trend canvas (bar sparkline over time buckets)
# --------------------------------------------------------------------------- #
class _TrendCanvas(tk.Canvas):
    """A row of slim bars, one per time bucket; ``None`` buckets show a gap."""

    HEIGHT = 56

    def __init__(self, master, values: list[float | None], *,
                 vmax: float, color_for):
        width = TREND_BUCKETS * 14
        super().__init__(
            master, width=width, height=self.HEIGHT,
            highlightthickness=0, bd=0, bg=COLORS["card"],
        )
        slot = width / max(1, len(values))
        bar = max(4, int(slot) - 4)
        for index, value in enumerate(values):
            x0 = index * slot + (slot - bar) / 2
            if value is None:
                # No data: a faint baseline tick, not a zero-height lie.
                self.create_rectangle(
                    x0, self.HEIGHT - 3, x0 + bar, self.HEIGHT - 1,
                    fill=COLORS["border_2"], width=0,
                )
                continue
            frac = 0.0 if vmax <= 0 else max(0.0, min(1.0, value / vmax))
            h = max(2, int((self.HEIGHT - 6) * frac))
            self.create_rectangle(
                x0, self.HEIGHT - 3 - h, x0 + bar, self.HEIGHT - 3,
                fill=color_for(value), width=0,
            )


class _MiniBar(tk.Canvas):
    """A slim proportional bar (green portion = up) for room-uptime rows."""

    def __init__(self, master, pct: float | None, width: int = 110, height: int = 7):
        super().__init__(master, width=width, height=height,
                         highlightthickness=0, bd=0, bg=COLORS["card"])
        if pct is None:
            self.create_rectangle(0, 0, width, height, fill=COLORS["border"], width=0)
            return
        self.create_rectangle(0, 0, width, height, fill=COLORS["offline"], width=0)
        up = int(width * max(0.0, min(100.0, pct)) / 100.0)
        if up > 0:
            self.create_rectangle(0, 0, up, height, fill=COLORS["online"], width=0)


class _FillBar(tk.Canvas):
    """A proportional fill over a faint track — colour supplied by the caller.

    Unlike :class:`_MiniBar` (which encodes up/down with green-over-red), this
    is a neutral magnitude bar: the fill width is ``frac`` of the track and the
    colour means whatever the widget wants (azure for share-of-estate, a
    latency threshold colour, …).
    """

    def __init__(self, master, frac: float, color: str,
                 width: int = 120, height: int = 7):
        super().__init__(master, width=width, height=height,
                         highlightthickness=0, bd=0, bg=COLORS["card"])
        self.create_rectangle(0, 0, width, height, fill=COLORS["card_2"], width=0)
        fill = int(width * max(0.0, min(1.0, frac)))
        if fill > 0:
            self.create_rectangle(0, 0, fill, height, fill=color, width=0)


class _GaugeRing(tk.Canvas):
    """A 270° radial gauge: a faint track, a coloured value sweep, centred %.

    The opening sits at the bottom; the sweep runs clockwise from the lower-left
    over the top to the lower-right, so a full ring reads as 100%.
    """

    SIZE = 124
    _START = 225      # degrees; lower-left
    _EXTENT = -270    # clockwise sweep over the top

    def __init__(self, master, pct: float | None, *, color: str, caption: str):
        super().__init__(
            master, width=self.SIZE, height=self.SIZE,
            highlightthickness=0, bd=0, bg=COLORS["card"],
        )
        pad, thick = 13, 11
        box = (pad, pad, self.SIZE - pad, self.SIZE - pad)
        self.create_arc(*box, start=self._START, extent=self._EXTENT,
                        style="arc", outline=COLORS["border_2"], width=thick)
        if pct is not None and pct > 0:
            frac = max(0.0, min(1.0, pct / 100.0))
            self.create_arc(*box, start=self._START, extent=self._EXTENT * frac,
                            style="arc", outline=color, width=thick)
        cx = cy = self.SIZE / 2
        self.create_text(
            cx, cy - 3, text="—" if pct is None else f"{pct:.0f}%",
            fill=COLORS["text_faint"] if pct is None else color,
            font=(FONT_MONO, 21, "bold"),
        )
        self.create_text(
            cx, cy + 19, text=caption,
            fill=COLORS["text_faint"], font=(FONT_MONO, 8),
        )


class _StatusGrid(tk.Canvas):
    """A wall of cells — one dot per device, coloured by reachability.

    An estate-at-a-glance pulse: green/red/amber/grey cells laid out in a
    roughly-landscape grid. Capped at :data:`MAX_CELLS` so a thousand-device
    sweep can't grow an unbounded canvas (the widget notes the remainder).
    """

    CELL = 12
    GAP = 4
    MAX_CELLS = 480

    def __init__(self, master, statuses: list[DeviceStatus]):
        shown = statuses[:self.MAX_CELLS]
        columns = min(40, max(8, math.ceil(math.sqrt(len(shown)) * 1.7)))
        rows = max(1, math.ceil(len(shown) / columns))
        step = self.CELL + self.GAP
        width = max(self.CELL, columns * step - self.GAP)
        height = max(self.CELL, rows * step - self.GAP)
        super().__init__(
            master, width=width, height=height,
            highlightthickness=0, bd=0, bg=COLORS["card"],
        )
        for index, status in enumerate(shown):
            r, c = divmod(index, columns)
            x0, y0 = c * step, r * step
            self.create_oval(
                x0, y0, x0 + self.CELL, y0 + self.CELL,
                fill=STATUS_COLORS.get(status, COLORS["unknown"]), width=0,
            )


# --------------------------------------------------------------------------- #
# Widget card chrome
# --------------------------------------------------------------------------- #
class _WidgetCard(ctk.CTkFrame):
    """Shared frame for every widget: title bar with move/remove controls."""

    def __init__(self, master, view: "DashboardsView", spec: WidgetSpec, index: int):
        super().__init__(
            master, corner_radius=CORNER_LG, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        self.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            head, text="", image=icon(spec.icon, 13, COLORS["text_faint"]),
        ).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkLabel(
            head, text=spec.name.upper(), anchor="w",
            font=font(10, mono=True, weight="bold"), text_color=COLORS["text_faint"],
        ).grid(row=0, column=1, sticky="ew")
        for col, (glyph, action) in enumerate((
            ("◀", lambda: view.move_widget(index, -1)),
            ("▶", lambda: view.move_widget(index, +1)),
            ("✕", lambda: view.remove_widget(index)),
        ), start=2):
            ctk.CTkButton(
                head, text=glyph, width=22, height=20, corner_radius=CORNER,
                fg_color="transparent", hover_color=COLORS["card_hover"],
                text_color=COLORS["text_faint"], font=font(10),
                command=action,
            ).grid(row=0, column=col, padx=(2, 0))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(6, 12))
        self.body.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)


# --------------------------------------------------------------------------- #
# Add-widget picker
# --------------------------------------------------------------------------- #
class AddWidgetDialog(ctk.CTkToplevel):
    """Catalogue picker: one row per widget type, ADD disabled when on board."""

    def __init__(self, app, view: "DashboardsView"):
        super().__init__(app)
        self.title("Add widget")
        self.configure(fg_color=COLORS["bg"])
        self.geometry(f"560x520+{app.winfo_rootx() + 140}+{app.winfo_rooty() + 90}")
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text="WIDGET CATALOGUE", anchor="w",
            font=font(11, mono=True, weight="bold"), text_color=COLORS["text_faint"],
        ).grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 6))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=PAD - 4, pady=(0, PAD))
        body.grid_columnconfigure(0, weight=1)
        on_board = set(view.layout)
        for row, spec in enumerate(WIDGETS):
            line = ctk.CTkFrame(
                body, corner_radius=CORNER, fg_color=COLORS["card"],
                border_width=1, border_color=COLORS["border"],
            )
            line.grid(row=row, column=0, sticky="ew", pady=3)
            line.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(
                line, text="", image=icon(spec.icon, 16, COLORS["text_muted"]), width=30,
            ).grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=10)
            ctk.CTkLabel(
                line, text=spec.name, anchor="w",
                font=font(13, weight="bold"), text_color=COLORS["text"],
            ).grid(row=0, column=1, sticky="ew", pady=(8, 0))
            ctk.CTkLabel(
                line, text=spec.desc, anchor="w",
                font=font(11), text_color=COLORS["text_muted"],
            ).grid(row=1, column=1, sticky="ew", pady=(0, 8))
            added = spec.id in on_board
            ctk.CTkButton(
                line, text="ON BOARD" if added else "ADD", width=86, height=28,
                font=font(10, mono=True, weight="bold"),
                state="disabled" if added else "normal",
                command=lambda s=spec: self._add(view, s),
                **(BTN_GHOST if added else BTN_SOLID),
            ).grid(row=0, column=2, rowspan=2, padx=12)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(60, lambda: (self.lift(), self.grab_set(), self.focus_force()))

    def _add(self, view: "DashboardsView", spec: WidgetSpec) -> None:
        self.destroy()
        view.add_widget(spec.id)


# --------------------------------------------------------------------------- #
# The dashboards view
# --------------------------------------------------------------------------- #
class DashboardsView(ctk.CTkScrollableFrame):
    """The user-composed board. Layout lives in ``AppState.dashboard_widgets``."""

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._last_refresh_ts = 0.0
        self.grid_columnconfigure(0, weight=1)
        saved = app.app_state.dashboard_widgets
        self.layout: list[str] = (
            [w for w in saved if widget_by_id(w)] if saved is not None
            else list(DEFAULT_LAYOUT)
        )
        self.render()

    # ------------------------------------------------------------------ #
    # Layout mutations (each persists, then repaints)
    # ------------------------------------------------------------------ #
    def add_widget(self, widget_id: str) -> None:
        if widget_by_id(widget_id) is None or widget_id in self.layout:
            return
        self.layout.append(widget_id)
        self._persist()
        self.render()

    def remove_widget(self, index: int) -> None:
        if 0 <= index < len(self.layout):
            self.layout.pop(index)
            self._persist()
            self.render()

    def move_widget(self, index: int, delta: int) -> None:
        target = index + delta
        if 0 <= index < len(self.layout) and 0 <= target < len(self.layout):
            self.layout[index], self.layout[target] = self.layout[target], self.layout[index]
            self._persist()
            self.render()

    def reset_layout(self) -> None:
        self.layout = list(DEFAULT_LAYOUT)
        self.app.app_state.dashboard_widgets = None  # back to "never customised"
        self.app.app_state.save()
        self.render()

    def open_picker(self) -> None:
        AddWidgetDialog(self.app, self)

    def _persist(self) -> None:
        self.app.app_state.dashboard_widgets = list(self.layout)
        self.app.app_state.save()

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        self._last_refresh_ts = time.monotonic()
        self.render()

    def refresh_if_stale(self) -> None:
        if time.monotonic() - self._last_refresh_ts >= _REFRESH_THROTTLE_S:
            self.refresh()

    def render(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        if not self.layout:
            self._render_empty()
            return

        board = ctk.CTkFrame(self, fg_color="transparent")
        board.grid(row=0, column=0, sticky="nsew")
        for col in range(GRID):
            board.grid_columnconfigure(col, weight=1, uniform="board")

        # Flow layout: each widget takes the next slot its span fits into.
        row = col = 0
        for index, widget_id in enumerate(self.layout):
            spec = widget_by_id(widget_id)
            if spec is None:
                continue
            span = min(spec.span, GRID)
            if col + span > GRID:
                row += 1
                col = 0
            card = _WidgetCard(board, self, spec, index)
            card.grid(
                row=row, column=col, columnspan=span,
                sticky="nsew", padx=4, pady=4,
            )
            self._fill(card.body, spec)
            col += span
            if col >= GRID:
                row += 1
                col = 0

    def _render_empty(self) -> None:
        hero = ctk.CTkFrame(
            self, corner_radius=CORNER_LG, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        hero.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(hero, text="", image=icon("dashboards", 44, COLORS["ghost"])).pack(pady=(56, 6))
        ctk.CTkLabel(
            hero, text="An empty board", font=font(15, weight="bold"),
            text_color=COLORS["text"],
        ).pack()
        ctk.CTkLabel(
            hero, text="Add KPI tiles, trend charts and live lists to build the "
            "monitoring view your duty needs.",
            font=font(12), text_color=COLORS["text_muted"],
        ).pack(pady=(2, 14))
        ctk.CTkButton(
            hero, text="+ ADD WIDGET", height=38, width=170,
            font=font(11, mono=True, weight="bold"), command=self.open_picker,
            **BTN_SOLID,
        ).pack(pady=(0, 56))

    # ------------------------------------------------------------------ #
    # Widget bodies
    # ------------------------------------------------------------------ #
    def _fill(self, body, spec: WidgetSpec) -> None:
        fill = getattr(self, f"_w_{spec.id}", None)
        if fill is not None:
            fill(body)

    def _kpi(self, body, value: str, accent: str, caption: str) -> None:
        ctk.CTkLabel(
            body, text=value, anchor="w",
            font=font(30, mono=True, weight="bold"), text_color=accent,
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            body, text=caption, anchor="w",
            font=font(10), text_color=COLORS["text_faint"],
        ).grid(row=1, column=0, sticky="w")

    def _w_kpi_online(self, body) -> None:
        online = sum(1 for d in self.app.site.all_devices()
                     if d.status is DeviceStatus.ONLINE)
        self._kpi(body, str(online), KPI_GOOD if online else KPI_NEUTRAL, "reachable now")

    def _w_kpi_offline(self, body) -> None:
        offline = sum(1 for d in self.app.site.all_devices()
                      if d.status is DeviceStatus.OFFLINE)
        self._kpi(body, str(offline), KPI_BAD if offline else KPI_NEUTRAL, "unreachable now")

    def _w_kpi_devices(self, body) -> None:
        rooms = len(self.app.site.rooms)
        count = sum(1 for _ in self.app.site.all_devices())
        self._kpi(body, str(count), COLORS["text"], f"devices in {rooms} rooms")

    def _w_kpi_rooms(self, body) -> None:
        rooms = [r for r in self.app.site.rooms if r.devices]
        healthy = sum(1 for r in rooms if r.room_health is DeviceStatus.ONLINE)
        accent = KPI_GOOD if rooms and healthy == len(rooms) else COLORS["text"]
        self._kpi(body, f"{healthy}/{len(rooms)}" if rooms else "0", accent, "rooms fully online")

    def _w_kpi_latency(self, body) -> None:
        lats = [d.last_latency_ms for d in self.app.site.all_devices()
                if d.last_latency_ms is not None]
        value = f"{int(sum(lats) / len(lats))} ms" if lats else "—"
        self._kpi(body, value, COLORS["text"], "average probe latency")

    def _w_kpi_recording(self, body) -> None:
        recorders = [d for d in self.app.site.all_devices() if isinstance(d, Recorder)]
        recording = sum(1 for d in recorders
                        if d.recording_status is RecordingStatus.RECORDING)
        accent = COLORS["rec"] if recording else KPI_NEUTRAL
        self._kpi(body, f"{recording}/{len(recorders)}" if recorders else "—",
                  accent, "recorders capturing")

    def _w_kpi_uptime(self, body) -> None:
        values = self.app.history.uptime_buckets(TREND_WINDOW_S, TREND_BUCKETS)
        known = [v for v in values if v is not None]
        if not known:
            self._kpi(body, "—", KPI_NEUTRAL, "no history yet")
            return
        avg = sum(known) / len(known)
        accent = (KPI_GOOD if avg >= 99.0
                  else COLORS["warn"] if avg >= 90.0 else KPI_BAD)
        self._kpi(body, f"{avg:.1f}%", accent, "estate uptime · 24h")

    def _w_health_gauge(self, body) -> None:
        resolved = [d for d in self.app.site.all_devices() if d.status.is_resolved]
        online = sum(1 for d in resolved if d.status is DeviceStatus.ONLINE)
        pct = (online * 100.0 / len(resolved)) if resolved else None
        if pct is None:
            color = COLORS["text_faint"]
        elif pct >= 99.0:
            color = COLORS["online"]
        elif pct >= 90.0:
            color = COLORS["warn"]
        else:
            color = COLORS["offline"]
        _GaugeRing(body, pct, color=color, caption="ONLINE").grid(
            row=0, column=0, pady=(2, 0))
        ctk.CTkLabel(
            body,
            text=f"{online}/{len(resolved)} reachable now" if resolved else "run a sweep",
            font=font(10), text_color=COLORS["text_faint"],
        ).grid(row=1, column=0, pady=(4, 0))

    def _w_uptime_trend(self, body) -> None:
        values = self.app.history.uptime_buckets(TREND_WINDOW_S, TREND_BUCKETS)
        if not any(v is not None for v in values):
            self._empty(body, "No history yet — run a sweep.")
            return

        def color_for(pct: float) -> str:
            if pct >= 99.0:
                return COLORS["online"]
            return COLORS["warn"] if pct >= 90.0 else COLORS["offline"]

        _TrendCanvas(body, values, vmax=100.0, color_for=color_for).grid(
            row=0, column=0, sticky="w")
        known = [v for v in values if v is not None]
        ctk.CTkLabel(
            body, text=f"window avg {sum(known) / len(known):.1f}%   ·   1 bar = 1 hour",
            anchor="w", font=font(10, mono=True), text_color=COLORS["text_faint"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _w_latency_trend(self, body) -> None:
        values = self.app.history.latency_buckets(TREND_WINDOW_S, TREND_BUCKETS)
        known = [v for v in values if v is not None]
        if not known:
            self._empty(body, "No latency history yet — run a sweep.")
            return
        vmax = max(known)
        _TrendCanvas(
            body, values, vmax=vmax if vmax > 0 else 1.0,
            color_for=lambda _v: COLORS["text_muted"],
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            body,
            text=f"window avg {sum(known) / len(known):.0f} ms   ·   "
                 f"peak {vmax:.0f} ms   ·   1 bar = 1 hour",
            anchor="w", font=font(10, mono=True), text_color=COLORS["text_faint"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

    def _w_offline_list(self, body) -> None:
        down = [
            (room, device)
            for room in self.app.site.rooms
            for device in room.devices
            if device.status is DeviceStatus.OFFLINE
        ]
        if not down:
            self._empty(body, "All checked devices are online.")
            return
        for row, (room, device) in enumerate(down[:MAX_ROWS]):
            self._list_row(
                body, row, f"● {device.name}", room.name.upper(),
                COLORS["offline"], lambda r=room: self.app.open_room_from_dashboard(r),
            )
        if len(down) > MAX_ROWS:
            self._empty(body, f"+ {len(down) - MAX_ROWS} more offline…", row=MAX_ROWS)

    def _w_recorders(self, body) -> None:
        recorders = [
            (room, device)
            for room in self.app.site.rooms
            for device in room.devices
            if isinstance(device, Recorder)
        ]
        if not recorders:
            self._empty(body, "No recorders configured.")
            return
        for row, (room, device) in enumerate(recorders[:MAX_ROWS]):
            self._list_row(
                body, row, device.name,
                f"{room.name.upper()}   {recording_status_label(device.recording_status)}",
                recording_status_color(device.recording_status),
                lambda r=room: self.app.open_room_from_dashboard(r),
            )

    def _w_status_grid(self, body) -> None:
        statuses = [d.status for d in self.app.site.all_devices()]
        if not statuses:
            self._empty(body, "No devices configured.")
            return
        _StatusGrid(body, statuses).grid(row=0, column=0, sticky="w")
        online = sum(1 for s in statuses if s is DeviceStatus.ONLINE)
        offline = sum(1 for s in statuses if s is DeviceStatus.OFFLINE)
        pending = len(statuses) - online - offline
        legend = (f"{online} online   ·   {offline} offline   ·   "
                  f"{pending} unchecked   ·   1 cell = 1 device")
        if len(statuses) > _StatusGrid.MAX_CELLS:
            legend += f"   ·   first {_StatusGrid.MAX_CELLS} shown"
        ctk.CTkLabel(
            body, text=legend, anchor="w",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

    def _w_slowest_devices(self, body) -> None:
        rows = [
            (room, device)
            for room in self.app.site.rooms
            for device in room.devices
            if device.last_latency_ms is not None
            and device.status is DeviceStatus.ONLINE
        ]
        if not rows:
            self._empty(body, "No latency readings yet — run a sweep.")
            return
        rows.sort(key=lambda rd: rd[1].last_latency_ms or 0.0, reverse=True)
        top = rows[:MAX_ROWS]
        vmax = max((d.last_latency_ms or 0.0) for _, d in top) or 1.0
        for row, (room, device) in enumerate(top):
            lat = device.last_latency_ms or 0.0
            color = (COLORS["online"] if lat < 80.0
                     else COLORS["warn"] if lat < 250.0 else COLORS["offline"])
            line = ctk.CTkFrame(body, fg_color="transparent", corner_radius=CORNER)
            line.grid(row=row, column=0, sticky="ew", pady=1)
            line.grid_columnconfigure(0, weight=1)
            name = ctk.CTkLabel(
                line, text=device.name, anchor="w",
                font=font(12), text_color=COLORS["text"],
            )
            name.grid(row=0, column=0, sticky="ew")
            _FillBar(line, lat / vmax, color).grid(row=0, column=1, padx=(GAP, 8))
            val = ctk.CTkLabel(
                line, text=f"{lat:.0f} ms", width=58, anchor="e",
                font=font(11, mono=True, weight="bold"), text_color=color,
            )
            val.grid(row=0, column=2)
            for widget in (line, name, val):
                widget.bind("<Button-1>", lambda _e, r=room: self.app.open_room_from_dashboard(r))
                widget.configure(cursor="hand2")

    def _w_device_mix(self, body) -> None:
        groups: dict[str, list] = {}
        for device in self.app.site.all_devices():
            groups.setdefault(device.category, []).append(device)
        if not groups:
            self._empty(body, "No devices configured.")
            return
        ranked = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)
        most = max(len(devs) for _, devs in ranked)
        for row, (category, devs) in enumerate(ranked[:MAX_ROWS]):
            offline = sum(1 for d in devs if d.status is DeviceStatus.OFFLINE)
            line = ctk.CTkFrame(body, fg_color="transparent")
            line.grid(row=row, column=0, sticky="ew", pady=1)
            line.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                line, text=category, anchor="w",
                font=font(12), text_color=COLORS["text"],
            ).grid(row=0, column=0, sticky="ew")
            _FillBar(line, len(devs) / most, COLORS["info"]).grid(
                row=0, column=1, padx=(GAP, 8))
            count = f"{len(devs)}" + (f"  ({offline}↓)" if offline else "")
            ctk.CTkLabel(
                line, text=count, width=64, anchor="e",
                font=font(11, mono=True, weight="bold"),
                text_color=COLORS["offline"] if offline else COLORS["text_muted"],
            ).grid(row=0, column=2)
        if len(ranked) > MAX_ROWS:
            self._empty(body, f"+ {len(ranked) - MAX_ROWS} more categories…", row=MAX_ROWS)

    def _w_room_uptime(self, body) -> None:
        rooms = [r for r in self.app.site.rooms if r.devices]
        uptimes = self.app.history.rooms_uptime([r.id for r in rooms], TREND_WINDOW_S)
        rows = sorted(
            ((room, uptimes.get(room.id)) for room in rooms),
            key=lambda rp: (rp[1] is not None, rp[1] if rp[1] is not None else 0.0),
        )[:MAX_ROWS]
        if not any(pct is not None for _, pct in rows):
            self._empty(body, "No uptime history yet — run a sweep.")
            return
        for row, (room, pct) in enumerate(rows):
            line = ctk.CTkFrame(body, fg_color="transparent")
            line.grid(row=row, column=0, sticky="ew", pady=1)
            line.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                line, text=room.name, anchor="w",
                font=font(12), text_color=COLORS["text"],
            ).grid(row=0, column=0, sticky="ew")
            _MiniBar(line, pct).grid(row=0, column=1, padx=(GAP, 8))
            ctk.CTkLabel(
                line, text="—" if pct is None else f"{pct:.0f}%", width=44, anchor="e",
                font=font(11, mono=True, weight="bold"),
                text_color=(
                    COLORS["text_faint"] if pct is None
                    else COLORS["online"] if pct >= 99.0
                    else COLORS["warn"] if pct >= 90.0 else COLORS["offline"]
                ),
            ).grid(row=0, column=2)

    def _w_activity(self, body) -> None:
        events = tail_audit(MAX_ROWS)
        if not events:
            self._empty(body, "No recent activity recorded.")
            return
        for row, event in enumerate(events):
            name = str(event.get("event", "event"))
            detail = event.get("room_name") or event.get("device_name") or event.get("control") or ""
            text = f"{name}  ·  {detail}" if detail else name
            self._list_row(body, row, text, str(event.get("user") or ""), COLORS["text_muted"], None)

    # ------------------------------------------------------------------ #
    # Small shared pieces
    # ------------------------------------------------------------------ #
    @staticmethod
    def _empty(body, text: str, row: int = 0) -> None:
        ctk.CTkLabel(
            body, text=text, anchor="w",
            font=font(12), text_color=COLORS["text_faint"],
        ).grid(row=row, column=0, sticky="w", pady=4)

    @staticmethod
    def _list_row(body, row: int, left: str, right: str, left_color: str, command) -> None:
        line = ctk.CTkFrame(body, fg_color="transparent", corner_radius=CORNER)
        line.grid(row=row, column=0, sticky="ew", pady=1)
        line.grid_columnconfigure(0, weight=1)
        left_lbl = ctk.CTkLabel(
            line, text=left, anchor="w",
            font=font(12), text_color=left_color,
        )
        left_lbl.grid(row=0, column=0, sticky="ew", padx=(2, 0))
        right_lbl = ctk.CTkLabel(
            line, text=right, anchor="e",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        right_lbl.grid(row=0, column=1, sticky="e", padx=(GAP, 2))
        if command is not None:
            for widget in (line, left_lbl, right_lbl):
                widget.bind("<Button-1>", lambda _e: command())
                widget.configure(cursor="hand2")
