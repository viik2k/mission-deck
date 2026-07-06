"""mission-deck desktop UI (CustomTkinter).

The visual shell — a dark, enterprise-style window with a top navigation bar
(brand, view tabs, attention badge, search, settings, operator chip), a slim
context bar (breadcrumb + per-view actions) and swappable views beneath. The
Rooms view pairs a room sidebar (grouped into collapsible city boxes) with a
main panel rendering the selected room's devices as a grid of cards grouped by
category.

Beyond browsing it provides:
  * a live async **status check** (cards flip green/red without freezing the UI),
  * optional **auto-refresh** of the current room,
  * **Open Web UIs** to launch every web device in a room,
  * per-device **control actions** (click a card) driven by config commands, and
  * a global **command palette** (Ctrl+K) to jump to any room, device or action.

It is also built for non-technical users: the last-opened config is remembered,
a friendly **welcome screen** replaces a bare file dialog, and a **Settings**
dialog exposes every preference (browser, timeouts, appearance) so nobody has
to edit JSON by hand.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from mission_deck import __app_name__, __version__
from mission_deck.activity import ActivityView
from mission_deck.browser import BrowserConfig, open_urls
from mission_deck.cloud import CloudView
from mission_deck.dashboard import DashboardView
from mission_deck.dashboards import DashboardsView
from mission_deck.history import HistoryStore, Sample
from mission_deck.icons import category_icon_name, icon
from mission_deck.logging_setup import audit, current_user, log_location, setup_logging
from mission_deck.plugins import PluginsView, plugin_by_id, tile_plugins
from mission_deck import report
from mission_deck.controls import DeviceControl, controls_for
from mission_deck.editors import (
    CommandEditorDialog,
    DeviceEditorDialog,
    RoomEditorDialog,
)
from mission_deck.network import (
    DEFAULT_MAX_CONCURRENCY,
    CheckResult,
    fetch_recording_status,
    http_get,
    run_status_checks,
)
from mission_deck.config import (
    ConfigError,
    example_config_path,
    find_config,
    load_config,
    save_config,
)
from mission_deck.models import (
    Device,
    DeviceConfigError,
    DeviceStatus,
    Recorder,
    RecordingStatus,
    Room,
    Site,
)
from mission_deck.palette import CommandPalette
from mission_deck.state import AppState
from mission_deck.live_view import LiveViewWindow
from mission_deck.stream import find_ffmpeg
from mission_deck.toast import Toaster
from mission_deck.theme import (
    CORNER,
    CORNER_LG,
    GAP,
    GRID_COLUMNS,
    NAV_HEIGHT,
    PAD,
    SIDEBAR_WIDTH,
    TOPBAR_HEIGHT,
    COLORS,
    recording_status_color,
    recording_status_label,
    status_color,
    status_label,
)
from mission_deck.ui import (
    BTN_DANGER,
    BTN_GHOST,
    BTN_OUTLINE,
    BTN_SOLID,
    SWITCH,
    PromptDialog,
    font,
    style,
)

# Always-present navigation entries for the top nav bar: (key, label). The
# icon name == key. Plugin-contributed tabs (e.g. Cloud Sync) are appended at
# runtime when their plugin is activated — see App._apply_plugin_tiles.
NAV_ITEMS: list[tuple[str, str]] = [
    ("overview", "Overview"),
    ("rooms", "Rooms"),
    ("dashboards", "Dashboards"),
    ("plugins", "Plugins"),
]


def command_count(device: Device) -> int:
    cmds = device.extra.get("commands")
    return len(cmds) if isinstance(cmds, list) else 0


def _initials(name: str) -> str:
    """Two-letter avatar initials from a login/user name."""

    parts = [p for p in name.replace(".", " ").replace("_", " ").split() if p]
    if not parts:
        return "··"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Sidebar room button
# --------------------------------------------------------------------------- #
class RoomButton(ctk.CTkButton):
    """A selectable room entry in the sidebar with a health dot."""

    def __init__(self, master, room: Room, command):
        self.room = room
        self._search_haystack = (
            f"{room.name} {room.city} {room.location} {room.id}"
        ).lower()
        self._selected = False
        super().__init__(
            master,
            text=f"  {room.name.upper()}",
            anchor="w",
            height=40,
            corner_radius=CORNER,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            text_color=COLORS["text_faint"],
            font=font(11, mono=True),
            command=lambda: command(room),
        )
        self._health_dot = ctk.CTkLabel(
            self,
            text="●",
            width=16,
            font=font(12),
            text_color=status_color(room.room_health),
            fg_color="transparent",
        )
        self._health_dot.place(relx=1.0, rely=0.5, anchor="e", x=-10)
        # Forward hover events from dot so button hover state stays active and
        # the dot's canvas bg stays in sync (prevents the visible square).
        self._health_dot.bind("<Enter>", self._on_enter)
        self._health_dot.bind("<Leave>", self._on_leave)

    def _on_enter(self, event=None):
        super()._on_enter(event)
        self._health_dot.configure(fg_color=COLORS["card_hover"])

    def _on_leave(self, event=None):
        super()._on_leave(event)
        # Keep the dot's canvas bg in sync with the (selected) pill fill so no
        # square shows through; transparent when the row is unselected.
        self._health_dot.configure(
            fg_color=COLORS["accent2_soft"] if self._selected else "transparent"
        )

    def refresh_health(self) -> None:
        self._health_dot.configure(text_color=status_color(self.room.room_health))

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        if selected:
            # Azure-tinted pill marks the room you're in (orientation).
            self.configure(
                fg_color=COLORS["accent2_soft"],
                text_color=COLORS["text"],
                font=font(11, mono=True, weight="bold"),
            )
            self._health_dot.configure(fg_color=COLORS["accent2_soft"])
        else:
            self.configure(
                fg_color="transparent",
                text_color=COLORS["text_faint"],
                font=font(11, mono=True),
            )
            self._health_dot.configure(fg_color="transparent")


# --------------------------------------------------------------------------- #
# Device card
# --------------------------------------------------------------------------- #
class DeviceCard(ctk.CTkFrame):
    """A single device tile showing identity, address and a status dot.

    Widgets are created once and *rebound* to a different device via
    :meth:`set_device`. Cards are pooled and reused across room switches so
    selecting a room never pays the (expensive) widget-construction cost — this
    is what keeps navigation snappy at 100+ rooms.
    """

    def __init__(self, master):
        super().__init__(
            master,
            corner_radius=CORNER,
            fg_color=COLORS["card"],
            border_width=1,
            border_color=COLORS["border"],
        )
        self.device: Device | None = None
        self._click_cb = None  # set by the pool; called with the device on click
        self.grid_columnconfigure(0, weight=1)

        # --- Top row: [name] .............. [status dot] -------------------- #
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        top.grid_columnconfigure(0, weight=1)

        self._name = ctk.CTkLabel(
            top, text="", anchor="w",
            font=font(13, weight="bold"), text_color=COLORS["text"],
        )
        self._name.grid(row=0, column=0, sticky="ew")
        self._model = ctk.CTkLabel(
            top, text="", anchor="w",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._model.grid(row=1, column=0, sticky="ew")

        self._dot = ctk.CTkLabel(
            top, text="●", width=14, font=font(11),
            text_color=status_color(DeviceStatus.UNKNOWN),
        )
        self._dot.grid(row=0, column=1, sticky="ne")
        # Keep icon attribute alive for set_device compatibility; not rendered.
        self._icon = self._dot

        # --- Meta row: [address] ........... [latency] --------------------- #
        meta = ctk.CTkFrame(self, fg_color="transparent")
        meta.grid(row=1, column=0, sticky="ew", padx=12, pady=(6, 0))
        meta.grid_columnconfigure(0, weight=1)
        self._addr = ctk.CTkLabel(
            meta, text="", anchor="w",
            font=font(11, mono=True), text_color=COLORS["text"],
        )
        self._addr.grid(row=0, column=0, sticky="w")
        self._lat = ctk.CTkLabel(
            meta, text="", anchor="e",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._lat.grid(row=0, column=1, sticky="e")

        # --- Foot row: [tags] ......... [web] [cmds] [rec] ----------------- #
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=12, pady=(6, 12))
        foot.grid_columnconfigure(0, weight=1)
        tags = ctk.CTkFrame(foot, fg_color="transparent")
        tags.grid(row=0, column=0, sticky="w")
        self._tags = [self._make_tag(tags) for _ in range(2)]

        pills = ctk.CTkFrame(foot, fg_color="transparent")
        pills.grid(row=0, column=1, sticky="e")
        self._web_pill = ctk.CTkLabel(
            pills, text="WEB",
            font=font(9, mono=True), text_color=COLORS["text_faint"],
        )
        self._cmd_pill = ctk.CTkLabel(
            pills, text="",
            font=font(9, mono=True), text_color=COLORS["text_faint"],
        )
        self._web_pill.pack(side="left", padx=(0, 8))
        self._cmd_pill.pack(side="left")
        # Recording pill — only shown for Recorder devices (hidden otherwise).
        self._recording = ctk.CTkLabel(
            foot, text="", anchor="e",
            font=font(9, mono=True, weight="bold"),
            text_color=recording_status_color(RecordingStatus.UNKNOWN),
        )

        # Hover affordance + click to open the device's control actions.
        self._hover_targets = (
            self, top, meta, foot, self._name, self._model,
            self._addr, self._lat, self._dot,
        )
        for widget in self._hover_targets:
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_click)
            widget.configure(cursor="hand2")

    @staticmethod
    def _make_tag(master) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            master, text="", corner_radius=4, height=16,
            fg_color="transparent", text_color=COLORS["text_faint"],
            font=font(9, mono=True),
        )

    def set_device(self, device: Device) -> None:
        """Rebind this (pooled) card to display ``device``."""

        self.device = device
        self._name.configure(text=device.name)
        self._model.configure(text=device.description)
        self._addr.configure(text=device.address)
        # Tag pills (up to two), reusing pooled labels.
        for index, label in enumerate(self._tags):
            if index < len(device.tags):
                label.configure(text=f" {device.tags[index].upper()} ")
                label.pack(side="left", padx=(0, 5))
            else:
                label.pack_forget()
        # Web + command pills.
        if device.is_web_accessible:
            self._web_pill.pack(side="left", padx=(0, 8))
        else:
            self._web_pill.pack_forget()
        cmds = command_count(device)
        if cmds:
            self._cmd_pill.configure(text=f"{cmds} CMD")
            self._cmd_pill.pack(side="left")
        else:
            self._cmd_pill.pack_forget()
        self.refresh()

    def refresh(self) -> None:
        """Repaint status-dependent bits (indicator colour, latency, border)."""

        if self.device is None:
            return
        status = self.device.status
        self._dot.configure(text_color=status_color(status))
        # Latency / timeout readout.
        if self.device.last_latency_ms is not None:
            self._lat.configure(text=f"{int(self.device.last_latency_ms)} ms")
        elif status is DeviceStatus.OFFLINE:
            self._lat.configure(text="— timeout")
        else:
            self._lat.configure(text="—")
        # Offline devices get a faint red wash + red outline so they stand out
        # in the grid; everything else sits on the neutral card surface.
        offline = status is DeviceStatus.OFFLINE
        self.configure(
            fg_color=COLORS["offline_soft"] if offline else COLORS["card"],
            border_color=COLORS["offline"] if offline else COLORS["border"],
        )
        # Recorders get a live recording-state pill; other devices hide it.
        if isinstance(self.device, Recorder):
            rec = self.device.recording_status
            self._recording.configure(
                text=recording_status_label(rec),
                text_color=recording_status_color(rec),
            )
            self._recording.grid(row=0, column=2, sticky="e", padx=(8, 0))
        else:
            self._recording.grid_remove()

    def _on_enter(self, _event=None) -> None:
        offline = self.device is not None and self.device.status is DeviceStatus.OFFLINE
        # Keep the red wash + outline on hover for offline cards; neutral cards
        # lift to the hover surface.
        self.configure(
            fg_color=COLORS["offline_soft"] if offline else COLORS["card_hover"],
            border_color=COLORS["offline"] if offline else COLORS["border_strong"],
        )

    def _on_leave(self, _event=None) -> None:
        offline = self.device is not None and self.device.status is DeviceStatus.OFFLINE
        self.configure(
            fg_color=COLORS["offline_soft"] if offline else COLORS["card"],
            border_color=COLORS["offline"] if offline else COLORS["border"],
        )

    def _on_click(self, _event=None) -> None:
        if self._click_cb is not None and self.device is not None:
            self._click_cb(self.device)


# --------------------------------------------------------------------------- #
# Sidebar city group (collapsible)
# --------------------------------------------------------------------------- #
class CityGroup(ctk.CTkFrame):
    """A collapsible box of rooms for one city in the sidebar.

    Room buttons are built **lazily** — only when the group is first expanded
    (or matched by a search). This is what keeps startup fast at 100+ rooms:
    constructing a few city headers is cheap; constructing every room button up
    front is not. Groups start collapsed; the app expands the first one.
    """

    @staticmethod
    def _haystack(room: Room) -> str:
        return f"{room.name} {room.city} {room.location} {room.id}".lower()

    def __init__(self, master, city: str, rooms: list[Room], app: "App"):
        super().__init__(master, fg_color="transparent")
        self.city = city
        self.rooms = rooms
        self._app = app
        self.collapsed = True
        self._built = False
        self._filtered_visible: int | None = None
        self.buttons: list[RoomButton] = []
        self.grid_columnconfigure(0, weight=1)

        self._header = ctk.CTkButton(
            self,
            text="",
            anchor="w",
            height=28,
            corner_radius=CORNER,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            text_color=COLORS["text_faint"],
            font=font(10, mono=True, weight="bold"),
            command=self.toggle,
        )
        self._header.grid(row=0, column=0, sticky="ew")

        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self._body.grid_columnconfigure(0, weight=1)
        self._body.grid_remove()  # hidden until expanded

        self._update_header()

    def _ensure_built(self) -> None:
        """Create this group's room buttons on first use."""

        if self._built:
            return
        for index, room in enumerate(self.rooms):
            btn = RoomButton(self._body, room, command=self._app.select_room)
            btn.grid(row=index, column=0, sticky="ew", pady=2, padx=(8, 0))
            self.buttons.append(btn)
        self._built = True
        self._app.register_room_buttons(self.buttons)

    def _update_header(self) -> None:
        chevron = "▸" if self.collapsed else "▾"
        count = self._filtered_visible if self._filtered_visible is not None else len(self.rooms)
        self._header.configure(text=f"  {chevron}  {self.city.upper()}   ({count})")

    def toggle(self) -> None:
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        self.collapsed = collapsed
        if collapsed:
            self._body.grid_remove()
        else:
            self._ensure_built()
            self._body.grid()
        self._update_header()

    def apply_filter(self, query: str) -> int:
        """Filter to rooms matching ``query``; return the visible count.

        Works without building widgets when there are no matches (we test the
        room data directly), so searching across a collapsed estate stays cheap.
        """

        if not query:
            # Reset: show the group, restore all built buttons, clear the count.
            self.grid()
            self._filtered_visible = None
            if self._built:
                for btn in self.buttons:
                    btn._filtered_out = False
                    btn.grid()
            self._update_header()
            return len(self.rooms)

        matches = [r for r in self.rooms if query in self._haystack(r)]
        self._filtered_visible = len(matches)
        if not matches:
            self.grid_remove()
            self._update_header()
            return 0

        self.set_collapsed(False)  # builds + shows
        self.grid()
        for btn in self.buttons:
            match = query in btn._search_haystack
            btn._filtered_out = not match
            if match:
                btn.grid()
            else:
                btn.grid_remove()
        self._update_header()
        return len(matches)


# --------------------------------------------------------------------------- #
# Device control dialog
# --------------------------------------------------------------------------- #
class DeviceControlDialog(ctk.CTkToplevel):
    """A small panel of control actions for one device.

    Buttons come from :func:`controls_for` — built-in (Open Web UI) plus any
    config-defined commands. Commands run on a background thread so the dialog
    (and the main window) never freeze.
    """

    def __init__(self, app: "App", device: Device):
        super().__init__(app)
        self.app = app
        self.device = device
        self.title(device.name)
        self.configure(fg_color=COLORS["panel"])
        self.geometry("440x560")
        self.transient(app)
        self._status_clear_job: str | None = None
        self.grid_columnconfigure(0, weight=1)

        # --- Drawer-style header: icon tile + name / category · room -------- #
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 6))
        head.grid_columnconfigure(1, weight=1)
        tile = ctk.CTkFrame(
            head, width=38, height=38, corner_radius=9,
            fg_color=COLORS["card_2"], border_width=1, border_color=COLORS["border"],
        )
        tile.grid(row=0, column=0, rowspan=2, padx=(0, GAP), sticky="n")
        tile.grid_propagate(False)
        ctk.CTkLabel(
            tile, text="", image=icon(category_icon_name(device.category), 20, COLORS["text_muted"]),
        ).place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(
            head, text=device.name, anchor="w",
            font=font(15, weight="bold"), text_color=COLORS["text"],
        ).grid(row=0, column=1, sticky="ew")
        room_name = app.current_room.name if app.current_room else device.category
        ctk.CTkLabel(
            head, text=f"{device.category} · {room_name}", anchor="w",
            font=font(11, mono=True), text_color=COLORS["text_faint"],
        ).grid(row=1, column=1, sticky="ew")

        # Status / monitor chip row.
        chips = ctk.CTkFrame(self, fg_color="transparent")
        chips.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, GAP))
        ctk.CTkLabel(
            chips, text=f"● {status_label(device.status)}",
            font=font(12, weight="bold"), text_color=status_color(device.status),
        ).pack(side="left", padx=(0, GAP))
        monitor = device.extra.get("monitor", device.protocol)
        ctk.CTkLabel(
            chips, text=f" monitor: {monitor} ", corner_radius=4, height=20,
            fg_color=COLORS["card_2"], text_color=COLORS["text_muted"],
            font=font(10, mono=True),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkLabel(
            chips, text=f" {device.protocol}://{device.address} ", corner_radius=4, height=20,
            fg_color=COLORS["card_2"], text_color=COLORS["text_muted"],
            font=font(10, mono=True),
        ).pack(side="left")

        self._actions = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._actions.grid(row=3, column=0, sticky="nsew", padx=PAD - 4, pady=0)
        self._actions.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)
        self._build_actions()

        # Editing bar: manage the device itself (config-writing actions).
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(GAP, 0))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            bar, text="EDIT DEVICE", width=120, command=self._edit_device,
            font=font(11, mono=True), **style(BTN_OUTLINE, text_color=COLORS["text_muted"]),
        ).grid(row=0, column=1, padx=(0, GAP))
        ctk.CTkButton(
            bar, text="REMOVE", width=90, command=self._remove_device,
            font=font(11, mono=True), **BTN_DANGER,
        ).grid(row=0, column=2)

        self._status = ctk.CTkLabel(
            self, text="", anchor="w",
            font=font(12), text_color=COLORS["text_muted"],
        )
        self._status.grid(row=5, column=0, sticky="ew", padx=PAD, pady=(GAP, PAD))

        self.after(60, self._focus)

    def _build_actions(self) -> None:
        """(Re)build the list of control buttons from the device's controls."""

        for child in self._actions.winfo_children():
            child.destroy()

        controls = controls_for(self.device, self.app.browser_cfg)
        row = 0
        # Cameras (any device, really) with a configured stream_url get the
        # live-video pop-out. Azure, not red: it is orientation, not alarm.
        if self.device.stream_url:
            ctk.CTkButton(
                self._actions, text="◉ LIVE VIEW", height=40, anchor="w",
                font=font(11, mono=True, weight="bold"),
                command=self._open_live_view,
                **style(BTN_OUTLINE, border_color=COLORS["accent2_line"],
                        text_color=COLORS["accent2_text"]),
            ).grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            row += 1
        if not controls:
            ctk.CTkLabel(
                self._actions,
                text="No control buttons yet.\n"
                     "Use “Add Command” below to create one.",
                anchor="w", justify="left",
                font=font(12), text_color=COLORS["text_muted"],
            ).grid(row=row, column=0, sticky="ew", padx=8, pady=8)
            row += 1
        for control in controls:
            is_web = control.kind == "web"
            line = ctk.CTkFrame(self._actions, fg_color="transparent")
            line.grid(row=row, column=0, sticky="ew", pady=4)
            line.grid_columnconfigure(0, weight=1)
            is_primary_web = is_web and control.label.lower().startswith("open")
            ctk.CTkButton(
                line, text=control.label.upper(), height=40, anchor="w",
                font=font(11, mono=True, weight="bold"),
                command=lambda c=control: self._run_control(c),
                **(BTN_SOLID if is_primary_web else BTN_OUTLINE),
            ).grid(row=0, column=0, sticky="ew", padx=(8, 0))
            # Config-driven commands get an inline "edit" affordance.
            if control.source is not None:
                ctk.CTkButton(
                    line, text="✎", width=40, height=40, corner_radius=CORNER,
                    fg_color="transparent", hover_color=COLORS["card_hover"],
                    border_width=1, border_color=COLORS["border"],
                    text_color=COLORS["text_muted"],
                    command=lambda s=control.source: self._edit_command(s),
                ).grid(row=0, column=1, padx=(6, 8))
            row += 1

        # Recorder devices get dedicated start/stop buttons driven by their
        # recording_start_url / recording_stop_url config keys.
        if isinstance(self.device, Recorder):
            row = self._build_recording_controls(row)

        # Always-present "add a command" button at the end of the list.
        ctk.CTkButton(
            self._actions, text="+ ADD COMMAND", height=38,
            font=font(11, mono=True), command=self._add_command,
            **style(BTN_OUTLINE, text_color=COLORS["text_muted"]),
        ).grid(row=row, column=0, sticky="ew", padx=8, pady=(8, 4))

    def _build_recording_controls(self, row: int) -> int:
        """Add recorder-specific Start/Stop buttons; return the next free row."""

        assert isinstance(self.device, Recorder)
        start_url = self.device.recording_start_url
        stop_url = self.device.recording_stop_url
        if not start_url and not stop_url:
            return row

        header = ctk.CTkLabel(
            self._actions, text="Recording", anchor="w",
            font=font(11, weight="bold"), text_color=COLORS["text_muted"],
        )
        header.grid(row=row, column=0, sticky="ew", padx=8, pady=(10, 2))
        row += 1

        if start_url:
            ctk.CTkButton(
                self._actions, text="● START RECORDING", height=40, anchor="w",
                font=font(11, mono=True, weight="bold"),
                command=lambda u=start_url: self._recording_action(u, "Start Recording"),
                **style(BTN_OUTLINE, border_color=COLORS["online"], text_color=COLORS["online"]),
            ).grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            row += 1
        if stop_url:
            ctk.CTkButton(
                self._actions, text="■ STOP RECORDING", height=40, anchor="w",
                font=font(11, mono=True, weight="bold"),
                command=lambda u=stop_url: self._recording_action(u, "Stop Recording"),
                **BTN_DANGER,
            ).grid(row=row, column=0, sticky="ew", padx=8, pady=4)
            row += 1
        return row

    def _recording_action(self, url: str, label: str) -> None:
        """Fire a recorder start/stop URL, then re-poll its recording state."""

        self._set_status(f"{label}…", duration_ms=0)
        timeout = self.app._effective_timeout()

        def on_done(ok: bool, message: str) -> None:
            self._set_status(
                message,
                text_color=COLORS["online"] if ok else COLORS["offline"],
            )
            # Refresh the live recording state so the card pill catches up.
            room = self.app.current_room
            if ok and isinstance(self.device, Recorder) and room is not None:
                self.app._poll_recording(self.device, room)

        self.app.run_background(lambda: http_get(url, timeout), on_done)

    def _open_live_view(self) -> None:
        # Close first: this dialog holds a modal grab that would otherwise
        # leave the pop-out window unclickable while the dialog is open.
        app, device, room = self.app, self.device, self.app.current_room
        self.destroy()
        app.open_live_view(room, device)

    def _add_command(self) -> None:
        CommandEditorDialog(self.app, self.device, on_saved=self._build_actions)

    def _edit_command(self, spec: dict) -> None:
        CommandEditorDialog(self.app, self.device, spec, on_saved=self._build_actions)

    def _edit_device(self) -> None:
        room = self.app.current_room
        if room is None:
            return
        self.destroy()
        DeviceEditorDialog(self.app, room, self.device)

    def _remove_device(self) -> None:
        room = self.app.current_room
        if room is None:
            return
        if not messagebox.askyesno(
            __app_name__, f"Remove '{self.device.name}' from this room?", parent=self
        ):
            return
        if self.device in room.devices:
            room.devices.remove(self.device)
        if self.app.persist_config():
            self.app.refresh_current_room()
            self.destroy()

    def _focus(self) -> None:
        # Window focus/grab can fail transiently on some window managers; it is
        # cosmetic, so we never let it bubble — but we record it for diagnosis.
        try:
            self.lift()
            self.grab_set()
            self.focus_force()
        except Exception:
            logger.debug("Could not focus control dialog", exc_info=True)

    def _set_status(self, text: str, text_color: str | None = None, duration_ms: int = 5000) -> None:
        if self._status_clear_job:
            self.after_cancel(self._status_clear_job)
            self._status_clear_job = None
        self._status.configure(text=text, text_color=text_color or COLORS["text_muted"])
        if duration_ms > 0:
            self._status_clear_job = self.after(duration_ms, self._clear_status)

    def _clear_status(self) -> None:
        self._status.configure(text="", text_color=COLORS["text_muted"])
        self._status_clear_job = None

    def _run_control(self, control: DeviceControl) -> None:
        value = None
        if control.prompt:
            value = PromptDialog(self, control.label, control.prompt).get_input()
            if value is None:  # cancelled
                return
        self._set_status(f"Running “{control.label}”…", duration_ms=0)
        device = self.device
        logger.info(
            "Running control '%s' (%s) on device %s (%s)",
            control.label, control.kind, device.id, device.host,
        )

        def on_done(ok: bool, message: str) -> None:
            # Audit every command attempt with its outcome — this is the record
            # of who operated which courtroom device, and whether it worked.
            audit(
                "device.command",
                device_id=device.id,
                device_name=device.name,
                device_type=device.type,
                host=device.host,
                control=control.label,
                kind=control.kind,
                prompted=control.prompt is not None,
                ok=ok,
                result=message,
            )
            if not ok:
                logger.warning(
                    "Control '%s' on %s failed: %s", control.label, device.id, message
                )
            self._set_status(
                message,
                text_color=COLORS["online"] if ok else COLORS["offline"],
            )

        self.app.run_background(lambda: control.run(value), on_done)


# --------------------------------------------------------------------------- #
# Settings dialog
# --------------------------------------------------------------------------- #
class SettingsDialog(ctk.CTkToplevel):
    """GUI for every preference, so users never edit JSON by hand.

    Organised into titled sections (monitoring / browser / overview / data /
    diagnostics) like a grown-up preferences pane; the diagnostics section
    surfaces the version, config source and a one-click "open logs folder".
    """

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.app_state = app.app_state
        self.title("Settings")
        self.configure(fg_color=COLORS["bg"])
        self.geometry("560x640")
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=PAD)
        body.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._body = body
        self._row = 0

        # --- Monitoring ------------------------------------------------- #
        self._section("Monitoring")
        self._timeout = ctk.StringVar(value=str(app._effective_timeout()))
        self._entry_row("Status check timeout (seconds)", self._timeout)
        self._refresh = ctk.StringVar(value=str(self.app_state.auto_refresh_seconds or 60))
        self._entry_row("Auto-refresh interval (seconds)", self._refresh)
        self._concurrency = ctk.StringVar(value=str(self.app_state.max_concurrent_checks or 0))
        self._entry_row("Max concurrent probes (0 = automatic)", self._concurrency)

        # --- Browser ---------------------------------------------------- #
        self._section("Browser")
        self._label("Browser (leave blank for system default)")
        browser_row = ctk.CTkFrame(body, fg_color="transparent")
        browser_row.grid(row=self._row, column=1, sticky="ew", pady=8)
        browser_row.grid_columnconfigure(0, weight=1)
        self._browser = ctk.StringVar(value=self.app_state.browser_path)
        ctk.CTkEntry(browser_row, textvariable=self._browser, fg_color=COLORS["card"]).grid(
            row=0, column=0, sticky="ew")
        ctk.CTkButton(
            browser_row, text="BROWSE…", width=80, command=self._browse_browser,
            font=font(11, mono=True), **BTN_GHOST,
        ).grid(row=0, column=1, padx=(GAP, 0))
        self._row += 1
        self._new_window = ctk.BooleanVar(value=self.app_state.browser_new_window)
        self._switch_row("Open web UIs in a new window", self._new_window)

        # --- Overview --------------------------------------------------- #
        self._section("Overview")
        self._start_dash = ctk.BooleanVar(value=self.app_state.start_on_dashboard)
        self._switch_row("Open on the dashboard at launch", self._start_dash)
        self._dash_poll = ctk.BooleanVar(value=self.app_state.dashboard_poll_enabled)
        self._switch_row("Dashboard background refresh", self._dash_poll)
        self._dash_secs = ctk.StringVar(value=str(self.app_state.dashboard_poll_seconds or 120))
        self._entry_row("Background refresh interval (seconds)", self._dash_secs)

        # --- Data & history ---------------------------------------------- #
        self._section("Data & history")
        self._retention = ctk.StringVar(value=str(self.app_state.history_retention_days or 30))
        self._entry_row("Keep uptime history for (days)", self._retention)
        self._label("Configuration file")
        ctk.CTkButton(
            body, text="OPEN A DIFFERENT CONFIG…", command=self._switch_config,
            font=font(11, mono=True), **BTN_GHOST,
        ).grid(row=self._row, column=1, sticky="ew", pady=8)
        self._row += 1

        # --- Diagnostics -------------------------------------------------- #
        self._section("Diagnostics")
        source = str(app.config_path) if app.config_path else "example data (demo)"
        self._info_row("Version", f"{__app_name__} v{__version__}")
        self._info_row("Config source", source)
        log_dir = log_location()
        self._info_row("Log folder", str(log_dir) if log_dir else "unavailable")
        if log_dir:
            self._label("Diagnostic & audit logs")
            ctk.CTkButton(
                body, text="OPEN LOGS FOLDER", command=lambda: self.app.open_folder(log_dir),
                font=font(11, mono=True), **BTN_GHOST,
            ).grid(row=self._row, column=1, sticky="ew", pady=8)
            self._row += 1

        # Buttons
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            buttons, text="CANCEL", width=100, command=self.destroy,
            font=font(11, mono=True), **BTN_GHOST,
        ).grid(row=0, column=1, padx=(0, GAP))
        ctk.CTkButton(
            buttons, text="SAVE", width=100, command=self._save,
            font=font(11, mono=True, weight="bold"), **BTN_SOLID,
        ).grid(row=0, column=2)

        self.after(60, lambda: (self.lift(), self.grab_set(), self.focus_force()))

    # ------------------------------------------------------------------ #
    # Form-building helpers
    # ------------------------------------------------------------------ #
    def _section(self, title: str) -> None:
        pady = (4, 2) if self._row == 0 else (18, 2)
        ctk.CTkLabel(
            self._body, text=title.upper(), anchor="w",
            font=font(10, mono=True, weight="bold"), text_color=COLORS["text_faint"],
        ).grid(row=self._row, column=0, sticky="w", pady=pady)
        self._row += 1
        ctk.CTkFrame(self._body, height=1, corner_radius=0, fg_color=COLORS["border"]).grid(
            row=self._row, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        self._row += 1

    def _label(self, text: str) -> None:
        ctk.CTkLabel(
            self._body, text=text, anchor="w",
            font=font(13), text_color=COLORS["text"],
        ).grid(row=self._row, column=0, sticky="w", padx=(0, GAP), pady=8)

    def _entry_row(self, text: str, variable: ctk.StringVar) -> None:
        self._label(text)
        ctk.CTkEntry(self._body, textvariable=variable, fg_color=COLORS["card"]).grid(
            row=self._row, column=1, sticky="ew", pady=8)
        self._row += 1

    def _switch_row(self, text: str, variable: ctk.BooleanVar) -> None:
        self._label(text)
        ctk.CTkSwitch(self._body, text="", variable=variable, **SWITCH).grid(
            row=self._row, column=1, sticky="w", pady=8)
        self._row += 1

    def _info_row(self, caption: str, value: str) -> None:
        self._label(caption)
        ctk.CTkLabel(
            self._body, text=value, anchor="w", justify="left", wraplength=300,
            font=font(11, mono=True), text_color=COLORS["text_muted"],
        ).grid(row=self._row, column=1, sticky="w", pady=8)
        self._row += 1

    # ------------------------------------------------------------------ #
    def _browse_browser(self) -> None:
        path = filedialog.askopenfilename(
            title="Select a browser executable",
            filetypes=[("Programs", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self._browser.set(path)

    def _switch_config(self) -> None:
        self.destroy()
        self.app.switch_config_dialog()

    def _save(self) -> None:
        try:
            timeout = float(self._timeout.get())
            if timeout <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(__app_name__, "Timeout must be a positive number.")
            return
        try:
            refresh = max(0, int(float(self._refresh.get())))
        except ValueError:
            messagebox.showerror(__app_name__, "Auto-refresh interval must be a whole number.")
            return
        try:
            concurrency = max(0, int(float(self._concurrency.get())))
        except ValueError:
            messagebox.showerror(__app_name__, "Max concurrent probes must be a whole number.")
            return
        try:
            dash_secs = max(0, int(float(self._dash_secs.get())))
        except ValueError:
            messagebox.showerror(__app_name__, "Background refresh interval must be a whole number.")
            return
        try:
            retention = max(1, int(float(self._retention.get())))
        except ValueError:
            messagebox.showerror(__app_name__, "History retention must be a whole number of days.")
            return

        self.app_state.ping_timeout_seconds = timeout
        self.app_state.auto_refresh_seconds = refresh
        self.app_state.max_concurrent_checks = concurrency
        self.app_state.browser_path = self._browser.get().strip()
        self.app_state.browser_new_window = bool(self._new_window.get())
        self.app_state.start_on_dashboard = bool(self._start_dash.get())
        self.app_state.dashboard_poll_enabled = bool(self._dash_poll.get())
        self.app_state.dashboard_poll_seconds = dash_secs
        self.app_state.history_retention_days = retention
        self.app_state.save()

        audit(
            "settings.change",
            ping_timeout_seconds=timeout,
            auto_refresh_seconds=refresh,
            max_concurrent_checks=concurrency,
            browser_path=self.app_state.browser_path,
            browser_new_window=self.app_state.browser_new_window,
            start_on_dashboard=self.app_state.start_on_dashboard,
            dashboard_poll_enabled=self.app_state.dashboard_poll_enabled,
            dashboard_poll_seconds=dash_secs,
            history_retention_days=retention,
        )
        logger.info(
            "Settings updated (timeout=%.1fs, auto_refresh=%ds, concurrency=%d, "
            "dash_poll=%s/%ds, retention=%dd)",
            timeout, refresh, concurrency,
            self.app_state.dashboard_poll_enabled, dash_secs, retention,
        )
        self.app.apply_settings()
        self.app.toaster.show("Settings saved", kind="success", duration_ms=3000)
        self.destroy()


# --------------------------------------------------------------------------- #
# Keyboard shortcuts reference (F1)
# --------------------------------------------------------------------------- #
SHORTCUTS: list[tuple[str, str]] = [
    ("Ctrl + K  /  Ctrl + P", "Open the command palette"),
    ("Ctrl + 1 … 4", "Switch view (Overview / Rooms / Dashboards / Plugins)"),
    ("Ctrl + F", "Filter the room list"),
    ("F5", "Re-probe the current view (room check or estate sweep)"),
    ("Ctrl + ,", "Open settings"),
    ("F1", "This shortcut reference"),
    ("Esc", "Close the palette or a dialog"),
]


class ShortcutsDialog(ctk.CTkToplevel):
    """A compact keyboard-shortcuts reference card (F1)."""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.title("Keyboard shortcuts")
        self.configure(fg_color=COLORS["bg"])
        self.resizable(False, False)
        self.transient(app)
        self.geometry(f"+{app.winfo_rootx() + 120}+{app.winfo_rooty() + 100}")
        self.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self, text="KEYBOARD SHORTCUTS",
            font=font(11, mono=True, weight="bold"), text_color=COLORS["text_faint"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=PAD + 4, pady=(PAD, 8))
        for index, (keys, what) in enumerate(SHORTCUTS, start=1):
            ctk.CTkLabel(
                self, text=keys, anchor="w", width=170,
                font=font(12, mono=True, weight="bold"), text_color=COLORS["text"],
            ).grid(row=index, column=0, sticky="w", padx=(PAD + 4, GAP), pady=3)
            ctk.CTkLabel(
                self, text=what, anchor="w",
                font=font(12), text_color=COLORS["text_muted"],
            ).grid(row=index, column=1, sticky="w", padx=(0, PAD + 4), pady=3)
        ctk.CTkButton(
            self, text="CLOSE", width=90, command=self.destroy,
            font=font(11, mono=True), **BTN_GHOST,
        ).grid(row=len(SHORTCUTS) + 1, column=1, sticky="e", padx=PAD + 4, pady=(12, PAD))

        self.bind("<Escape>", lambda _e: self.destroy())
        self.after(60, lambda: (self.lift(), self.focus_force()))


# --------------------------------------------------------------------------- #
# Top navigation tab
# --------------------------------------------------------------------------- #
class NavTab(ctk.CTkFrame):
    """One labelled tab in the top nav bar with an accent active-underline."""

    def __init__(self, master, key: str, label: str, icon_name: str, command):
        super().__init__(master, fg_color="transparent")
        self.key = key
        self.icon_name = icon_name
        self.grid_columnconfigure(0, weight=1)
        # width=20: let the icon + label decide the size (CTk treats ``width``
        # as a minimum and would otherwise inflate every tab to ~216px).
        self._btn = ctk.CTkButton(
            self, text=label.upper(), width=20, height=30, corner_radius=CORNER,
            image=icon(icon_name, 14, COLORS["text_faint"]), compound="left",
            fg_color="transparent", hover_color=COLORS["card"],
            text_color=COLORS["text_faint"], font=font(11, mono=True),
            command=command,
        )
        self._btn.grid(row=0, column=0, sticky="ew", padx=2)
        # width=10: CTkFrame defaults to 200px wide, which would inflate the
        # whole tab; sticky="ew" stretches it to the real tab width anyway.
        self._underline = ctk.CTkFrame(
            self, width=10, height=2, corner_radius=0, fg_color="transparent",
        )
        self._underline.grid(row=1, column=0, sticky="ew", padx=10, pady=(3, 0))

    def set_active(self, active: bool) -> None:
        color = COLORS["text"] if active else COLORS["text_faint"]
        self._btn.configure(
            text_color=color, image=icon(self.icon_name, 14, color),
        )
        # Azure underline marks "you are here" (orientation), leaving red for alerts.
        self._underline.configure(
            fg_color=COLORS["accent2"] if active else "transparent",
        )


# --------------------------------------------------------------------------- #
# Main application window
# --------------------------------------------------------------------------- #
class App(ctk.CTk):
    def __init__(
        self,
        site: Site,
        config_path: Path | None = None,
        state: AppState | None = None,
    ):
        super().__init__()
        self.site = site
        self.config_path = config_path
        logger.info(
            "App window initialising (%d room(s), config=%s)",
            len(site.rooms), config_path if config_path else "demo",
        )
        # NB: ``app_state`` (not ``state``) — Tk reserves ``state()`` as a method.
        self.app_state = state if state is not None else AppState.load()
        self.browser_cfg = self._effective_browser_cfg()
        # Persisted uptime history (best-effort; disabled cleanly if unwritable).
        self.history = HistoryStore.open()
        self.history.prune(self.app_state.history_retention_days)
        self.current_room: Room | None = None
        self._room_buttons: list[RoomButton] = []
        # room.id -> its sidebar button, so health-dot updates are O(1) rather
        # than a linear scan of every button on each probe result.
        self._room_button_index: dict[str, RoomButton] = {}
        self._city_groups: list[CityGroup] = []
        self._selected_button: RoomButton | None = None
        self._device_cards: dict[str, DeviceCard] = {}
        # Reusable widget pools (built lazily, reused across room switches).
        self._card_pool: list[DeviceCard] = []
        self._section_pool: list[ctk.CTkLabel] = []
        # Threshold above which opening web UIs asks for confirmation, so a
        # mis-click on a device-dense room can't spawn dozens of tabs.
        self._web_confirm_threshold = 12
        # Status-check state. ``_check_gen`` lets us ignore results from a
        # superseded run. Worker threads push results onto ``_result_queue``;
        # the Tk thread drains it on a timer (Tk is not thread-safe).
        self._check_gen = 0
        self._checking = False
        self._checking_room: Room | None = None
        # device.id -> Device for the room being checked, so applying a result
        # is an O(1) lookup instead of a linear scan per device (O(N²) per room).
        self._checking_room_index: dict[str, Device] = {}
        self._result_queue: "queue.Queue[tuple]" = queue.Queue()
        self._poll_interval_ms = 40
        # Auto-refresh + config-switch state.
        self._auto_refresh_job: str | None = None
        self._statusbar_clear_job: str | None = None
        self._search_debounce_job: str | None = None
        self.requested_config: Path | None = None
        # Dashboard / estate-wide sweep state. The sweep reuses the same
        # thread→queue→Tk-timer pattern as the room check, but on its own
        # queue/generation so the two never tread on each other.
        self._dashboard: DashboardView | None = None
        self._showing_dashboard = False
        self._sweeping = False
        self._sweep_gen = 0
        self._sweep_queue: "queue.Queue[tuple]" = queue.Queue()
        self._sweep_samples: list[Sample] = []
        self._sweep_index: dict[str, tuple[Room, Device]] = {}
        self._dashboard_poll_job: str | None = None
        self.last_sweep_time: datetime | None = None
        # Live sweep progress (drives the "sweeping N/M…" topbar readout) and
        # the offline set of the previous sweep (drives "went offline" toasts).
        self._sweep_total = 0
        self._sweep_done = 0
        self._last_offline_ids: set[str] | None = None
        # Room-view status filter: "all" | "online" | "offline".
        self._room_filter = "all"

        # --- Window chrome -------------------------------------------------- #
        ctk.set_appearance_mode(self.app_state.appearance or "dark")
        ctk.set_default_color_theme("blue")
        self.title(f"{__app_name__}  ·  AV Room Manager")
        self.geometry("1200x760")
        self.minsize(1000, 640)
        self.configure(fg_color=COLORS["bg"])
        self._restore_window_geometry()

        # Non-blocking notifications (sweep results, offline alerts, exports).
        self.toaster = Toaster(self)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)  # top nav bar (fixed)
        self.grid_rowconfigure(1, weight=1)  # main column (stretch)

        # View routing. Each nav tab maps to a frame swapped into the views
        # container; ``_view_frames`` caches the lazily-built ones.
        self._current_view = "rooms"
        self._view_frames: dict[str, ctk.CTkFrame] = {}
        self._plugins_view: PluginsView | None = None
        self._cloud_view: CloudView | None = None
        self._dashboards_view: DashboardsView | None = None
        self._palette: CommandPalette | None = None
        # The single live-video pop-out (one ffmpeg feed at a time).
        self._live_view: LiveViewWindow | None = None

        self._build_navbar()        # row 0: brand + tabs + global actions
        self._build_main_column()   # row 1: context bar + views container
        self._build_rooms_view()    # the always-present default view
        self._bind_shortcuts()

        # Select the first room so the room view is populated on launch.
        if self.site.rooms:
            self.select_room(self.site.rooms[0])
        else:
            self._show_empty_state()
            self.navigate("rooms")

        # Land on the overview unless the user turned that off.
        if self.app_state.start_on_dashboard:
            self.navigate("overview")

        # Honour saved auto-refresh + background-sweep preferences.
        self._reschedule_auto_refresh()
        self._reschedule_dashboard_poll()

    # ------------------------------------------------------------------ #
    # Top navigation bar (brand + tabs + global actions)
    # ------------------------------------------------------------------ #
    def _build_navbar(self) -> None:
        bar = ctk.CTkFrame(self, height=NAV_HEIGHT, corner_radius=0, fg_color=COLORS["rail"])
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(0, weight=1)
        bar.grid_columnconfigure(2, weight=1)  # spacer between tabs and actions

        # Brand mark + wordmark.
        brand = ctk.CTkFrame(bar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w", padx=(PAD, PAD + 6))
        logo = ctk.CTkFrame(brand, width=28, height=28, corner_radius=8, fg_color=COLORS["accent"])
        logo.pack(side="left")
        logo.pack_propagate(False)
        ctk.CTkLabel(logo, text="", image=icon("logo", 16, "#ffffff")).place(
            relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(
            brand, text="MISSION-DECK",
            font=font(12, mono=True, weight="bold"), text_color=COLORS["text"],
        ).pack(side="left", padx=(10, 0))

        # View tabs. Plugin tabs can be added/removed at runtime without
        # disturbing the rest of the bar (they pack into ``_nav_tab_host``).
        self._nav_tabs: dict[str, NavTab] = {}
        tabs = ctk.CTkFrame(bar, fg_color="transparent")
        tabs.grid(row=0, column=1, sticky="w")
        self._nav_tab_host = tabs
        for key, label in NAV_ITEMS:
            self._add_nav_tab(key, label)
        # Tabs for plugins the operator has already activated.
        self._apply_plugin_tiles()

        # Right cluster: attention badge · palette/search · settings · operator.
        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=3, sticky="e", padx=(0, PAD))

        # Offline-count badge — hidden while the estate is clean; clicking it
        # jumps to the overview's "needs attention" list.
        self._attention_badge = ctk.CTkButton(
            right, text="", height=30, corner_radius=999,
            fg_color="transparent", hover_color=COLORS["accent_soft"],
            border_width=1, border_color=COLORS["accent_line"],
            text_color=COLORS["accent_text"], font=font(10, mono=True, weight="bold"),
            command=lambda: self.navigate("overview"),
        )

        self._palette_btn = ctk.CTkButton(
            right, text="Search   CTRL+K", anchor="w",
            image=icon("search", 14, COLORS["text_faint"]), compound="left",
            width=20, height=32, corner_radius=CORNER,
            fg_color=COLORS["card"], hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"],
            text_color=COLORS["text_faint"], font=font(11),
            command=self.open_palette,
        )
        self._palette_btn.pack(side="left", padx=(GAP, GAP))

        ctk.CTkButton(
            right, text="", image=icon("settings", 17, COLORS["text_faint"]),
            width=34, height=32, corner_radius=CORNER,
            fg_color="transparent", hover_color=COLORS["card"],
            command=self.open_settings,
        ).pack(side="left", padx=(0, GAP))

        # Signed-in operator chip (the name every action is audited under);
        # clicking it opens the config picker.
        user = current_user()
        chip = ctk.CTkFrame(
            right, corner_radius=999, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        chip.pack(side="left")
        avatar = ctk.CTkLabel(
            chip, text=_initials(user), width=24, height=24, corner_radius=12,
            fg_color=COLORS["accent"], text_color="#ffffff",
            font=font(11, weight="bold"),
        )
        avatar.pack(side="left", padx=(4, 6), pady=4)
        name = ctk.CTkLabel(
            chip, text=user, font=font(12), text_color=COLORS["text_muted"],
        )
        name.pack(side="left", padx=(0, 10))
        for widget in (chip, avatar, name):
            widget.bind("<Button-1>", lambda _e: self.switch_config_dialog())
            widget.configure(cursor="hand2")

    def _add_nav_tab(self, key: str, label: str, icon_name: str | None = None) -> None:
        """Add a navigable tab to the nav bar (idempotent on ``key``)."""

        if key in self._nav_tabs:
            return
        tab = NavTab(self._nav_tab_host, key, label, icon_name or key,
                     lambda k=key: self.navigate(k))
        tab.pack(side="left", padx=2)
        self._nav_tabs[key] = tab

    def _remove_nav_tab(self, key: str) -> None:
        tab = self._nav_tabs.pop(key, None)
        if tab is not None:
            tab.destroy()

    def _apply_plugin_tiles(self) -> None:
        """Ensure every enabled tile-providing plugin has a nav tab."""

        for spec in tile_plugins():
            view_key, label, icon_name = spec.tile
            if self.is_plugin_enabled(spec.id):
                self._add_nav_tab(view_key, label, icon_name)
            else:
                self._remove_nav_tab(view_key)

    # ------------------------------------------------------------------ #
    # Plugin activation (drives the plugin nav tabs)
    # ------------------------------------------------------------------ #
    def is_plugin_enabled(self, plugin_id: str) -> bool:
        spec = plugin_by_id(plugin_id)
        if spec is not None and spec.builtin:
            return True
        return plugin_id in self.app_state.enabled_plugins

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Activate/deactivate a plugin: persist it and sync its nav tab."""

        spec = plugin_by_id(plugin_id)
        if spec is None or spec.builtin:
            return
        if self.is_plugin_enabled(plugin_id) == enabled:
            return
        current = [p for p in self.app_state.enabled_plugins if p != plugin_id]
        if enabled:
            current.append(plugin_id)
        self.app_state.enabled_plugins = sorted(current)
        self.app_state.save()
        audit("settings.change", plugin=plugin_id, enabled=enabled)

        if spec.tile is not None:
            view_key, label, icon_name = spec.tile
            if enabled:
                self._add_nav_tab(view_key, label, icon_name)
            else:
                # Leaving a now-hidden view: fall back to the Plugins screen.
                if self._current_view == view_key:
                    self.navigate("plugins")
                self._remove_nav_tab(view_key)
        self._update_nav_highlight()

    # ------------------------------------------------------------------ #
    # Main column = top bar + swappable views
    # ------------------------------------------------------------------ #
    def _build_main_column(self) -> None:
        main = ctk.CTkFrame(self, corner_radius=0, fg_color=COLORS["panel"])
        main.grid(row=1, column=0, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(1, weight=1)
        self._main_column = main

        self._build_topbar(main)

        self._views = ctk.CTkFrame(main, corner_radius=0, fg_color=COLORS["panel"])
        self._views.grid(row=1, column=0, sticky="nsew")
        self._views.grid_columnconfigure(0, weight=1)
        self._views.grid_rowconfigure(0, weight=1)

    def _build_topbar(self, master) -> None:
        """The context bar: breadcrumb on the left, per-view actions on the right."""

        bar = ctk.CTkFrame(
            master, height=TOPBAR_HEIGHT, corner_radius=0, fg_color=COLORS["panel"],
        )
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        self._crumb = ctk.CTkLabel(
            bar, text="MISSION-DECK", anchor="w",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._crumb.grid(row=0, column=0, sticky="w", padx=(PAD, GAP))

        # Room-filter text — the sidebar's filter entry binds to this; typing
        # anything jumps to the Rooms view (see _on_search_changed).
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._on_search_changed())

        # Per-view action host: one frame per view, swapped on navigate.
        self._action_host = ctk.CTkFrame(bar, fg_color="transparent")
        self._action_host.grid(row=0, column=1, sticky="e", padx=(0, PAD))
        self._build_room_actions(self._action_host)
        self._build_overview_actions(self._action_host)
        self._build_dashboards_actions(self._action_host)

    def _build_room_actions(self, host) -> None:
        frame = ctk.CTkFrame(host, fg_color="transparent")
        self._room_actions = frame
        self._check_btn = ctk.CTkButton(
            frame, text="CHECK STATUS", width=140, height=32,
            font=font(11, mono=True, weight="bold"), command=self.on_check_status,
            **BTN_SOLID,
        )
        self._check_btn.pack(side="right")
        self._open_btn = ctk.CTkButton(
            frame, text="OPEN WEB UIS", width=130, height=32,
            font=font(11, mono=True), command=self.on_open_web_uis,
            **BTN_OUTLINE,
        )
        self._open_btn.pack(side="right", padx=(0, GAP))
        self._add_device_btn = ctk.CTkButton(
            frame, text="+ DEVICE", width=90, height=32,
            font=font(11, mono=True), command=self.add_device,
            **style(BTN_OUTLINE, text_color=COLORS["text_muted"]),
        )
        self._add_device_btn.pack(side="right", padx=(0, GAP))
        self._edit_room_btn = ctk.CTkButton(
            frame, text="EDIT", width=48, height=32,
            font=font(10, mono=True), command=self.edit_current_room,
            **style(BTN_OUTLINE, text_color=COLORS["text_faint"]),
        )
        self._edit_room_btn.pack(side="right", padx=(0, GAP))
        self._auto_var = ctk.BooleanVar(value=self.app_state.auto_refresh_enabled)
        self._auto_switch = ctk.CTkSwitch(
            frame, text="Auto", variable=self._auto_var, command=self.on_toggle_auto_refresh,
            font=font(12), text_color=COLORS["text_muted"], **SWITCH,
        )
        self._auto_switch.pack(side="right", padx=(0, GAP + 4))

    def _build_overview_actions(self, host) -> None:
        frame = ctk.CTkFrame(host, fg_color="transparent")
        self._overview_actions = frame
        self._ov_refresh_btn = ctk.CTkButton(
            frame, text="REFRESH ALL", width=130, height=32,
            font=font(11, mono=True, weight="bold"), command=self.run_estate_sweep,
            **BTN_SOLID,
        )
        self._ov_refresh_btn.pack(side="right")
        self._ov_export_btn = ctk.CTkButton(
            frame, text="EXPORT", width=84, height=32,
            font=font(11, mono=True), command=self.on_export_report,
            **style(BTN_OUTLINE, text_color=COLORS["text_muted"]),
        )
        self._ov_export_btn.pack(side="right", padx=(0, GAP))
        self._ov_auto_var = ctk.BooleanVar(value=self.app_state.dashboard_poll_enabled)
        ctk.CTkSwitch(
            frame, text="Auto", variable=self._ov_auto_var,
            command=lambda: self.on_toggle_dashboard_poll(bool(self._ov_auto_var.get())),
            font=font(12), text_color=COLORS["text_muted"], **SWITCH,
        ).pack(side="right", padx=(0, GAP + 4))
        self._ov_sweep_label = ctk.CTkLabel(
            frame, text="", anchor="e",
            font=font(11), text_color=COLORS["text_faint"],
        )
        self._ov_sweep_label.pack(side="right", padx=(0, GAP + 4))

    def _build_dashboards_actions(self, host) -> None:
        frame = ctk.CTkFrame(host, fg_color="transparent")
        self._dashboards_actions = frame
        ctk.CTkButton(
            frame, text="+ WIDGET", width=110, height=32,
            font=font(11, mono=True, weight="bold"), command=self.open_widget_picker,
            **BTN_SOLID,
        ).pack(side="right")
        ctk.CTkButton(
            frame, text="RESET LAYOUT", width=120, height=32,
            font=font(11, mono=True), command=self._reset_dashboard_layout,
            **style(BTN_OUTLINE, text_color=COLORS["text_muted"]),
        ).pack(side="right", padx=(0, GAP))

    def open_widget_picker(self) -> None:
        """Jump to the Dashboards board and open the widget catalogue."""

        self.navigate("dashboards")
        if self._dashboards_view is not None:
            self._dashboards_view.open_picker()

    def _reset_dashboard_layout(self) -> None:
        if self._dashboards_view is None:
            return
        if messagebox.askyesno(
            __app_name__, "Reset the dashboard to the default layout?", parent=self
        ):
            self._dashboards_view.reset_layout()

    def _refresh_dashboards_board(self) -> None:
        """Repaint the custom board when it is the active view (cheap guard)."""

        if self._dashboards_view is not None and self._current_view == "dashboards":
            self._dashboards_view.refresh()

    # ------------------------------------------------------------------ #
    # View router
    # ------------------------------------------------------------------ #
    def navigate(self, view: str) -> None:
        if view == "settings":
            self.open_settings()
            return
        # Plugin-gated screens only exist while their plugin is activated.
        if view == "cloud" and not self.is_plugin_enabled("cloud_sync"):
            view = "plugins"
        if view == "activity" and not self.is_plugin_enabled("activity_log"):
            view = "plugins"
        frame = self._frame_for(view)
        if frame is None:
            return
        old_view = self._current_view
        # Views are stacked in the same grid cell and raised into view — far
        # cheaper than unmapping/remapping frames, which forces a full geometry
        # recompute (the source of the visible flicker on page changes).
        # CTkScrollableFrame forwards grid() to its outer container but not
        # lift()/winfo_manager(), so address the outer widget directly.
        outer = getattr(frame, "_parent_frame", frame)
        if not outer.winfo_manager():
            frame.grid(row=0, column=0, sticky="nsew")
        outer.lift()
        self._current_view = view
        self._showing_dashboard = (view == "overview")
        if view == "overview":
            self._ensure_overview().refresh_if_stale()
        elif view == "dashboards" and self._dashboards_view is not None:
            self._dashboards_view.refresh_if_stale()
        self._update_topbar()
        self._update_nav_highlight(old_view)

    def _frame_for(self, view: str) -> ctk.CTkFrame | None:
        if view == "rooms":
            return self._room_panel
        if view == "overview":
            return self._ensure_overview()
        if view == "plugins":
            return self._ensure_simple_view("plugins")
        if view == "cloud":
            return self._ensure_simple_view("cloud")
        if view == "activity":
            return self._ensure_simple_view("activity")
        if view == "dashboards":
            return self._ensure_simple_view("dashboards")
        return None

    def _ensure_overview(self) -> DashboardView:
        if self._dashboard is None:
            self._dashboard = DashboardView(self._views, self)
            self._view_frames["overview"] = self._dashboard
        return self._dashboard

    def _ensure_simple_view(self, view: str) -> ctk.CTkFrame:
        existing = self._view_frames.get(view)
        if existing is not None:
            return existing
        if view == "plugins":
            frame = PluginsView(self._views, self)
        elif view == "cloud":
            frame = CloudView(self._views, self)
        elif view == "activity":
            frame = ActivityView(self._views, self)
        else:
            frame = DashboardsView(self._views, self)
            self._dashboards_view = frame
        self._view_frames[view] = frame
        return frame

    def _update_topbar(self) -> None:
        titles = {
            "overview": "OVERVIEW", "rooms": "ROOMS", "dashboards": "DASHBOARDS",
            "plugins": "PLUGINS", "cloud": "CLOUD SYNC", "activity": "ACTIVITY",
        }
        title = titles.get(self._current_view, "")
        crumb = f"MISSION-DECK  ›  {title}"
        if self._current_view == "rooms" and self.current_room is not None:
            crumb += f"  ›  {self.current_room.name.upper()}"
        self._crumb.configure(text=crumb)
        self._room_actions.grid_remove()
        self._overview_actions.grid_remove()
        self._dashboards_actions.grid_remove()
        if self._current_view == "rooms":
            self._room_actions.grid(row=0, column=0, sticky="e")
        elif self._current_view == "overview":
            self._overview_actions.grid(row=0, column=0, sticky="e")
            self._refresh_overview_sweep_label()
        elif self._current_view == "dashboards":
            self._dashboards_actions.grid(row=0, column=0, sticky="e")

    def _set_overview_sweeping(self, sweeping: bool) -> None:
        """Reflect an in-progress estate sweep in the overview topbar controls."""

        if sweeping:
            self._ov_refresh_btn.configure(state="disabled", text="SWEEPING…")
            self._ov_sweep_label.configure(text="checking all rooms…")
        else:
            self._ov_refresh_btn.configure(state="normal", text="REFRESH ALL")
            self._refresh_overview_sweep_label()

    def _refresh_overview_sweep_label(self) -> None:
        ts = self.last_sweep_time
        self._ov_sweep_label.configure(
            text=f"last sweep {ts.strftime('%H:%M:%S')}" if ts else "no sweep yet"
        )

    def _flash_overview_status(self, text: str, duration_ms: int = 5000) -> None:
        """Show a transient message in the overview topbar, then restore it."""

        self._ov_sweep_label.configure(text=text)

        def restore() -> None:
            if not self._sweeping:
                self._refresh_overview_sweep_label()

        self.after(duration_ms, restore)

    def on_export_report(self) -> None:
        """Save an estate status CSV: one row per device with its 24h uptime."""

        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export status report",
            defaultextension=".csv",
            initialfile=report.default_filename(),
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        def work() -> str:
            count = report.write_csv(path, self.site, self.history)
            return f"exported {count} devices to {Path(path).name}"

        def done(ok: bool, message: str) -> None:
            if ok:
                audit("report.export", path=path)
                self._flash_overview_status(message)
                self.toaster.show("Report exported", message, kind="success")
            else:
                messagebox.showerror("Export failed", message, parent=self)

        self.run_background(work, done)

    # ------------------------------------------------------------------ #
    # Rooms view (sidebar tree + room detail)
    # ------------------------------------------------------------------ #
    def _build_rooms_view(self) -> None:
        panel = ctk.CTkFrame(self._views, corner_radius=0, fg_color=COLORS["panel"])
        self._room_panel = panel
        self._view_frames["rooms"] = panel
        panel.grid_columnconfigure(1, weight=1)
        panel.grid_rowconfigure(0, weight=1)
        self._build_room_detail()   # column 1
        self._build_sidebar()       # column 0 (rebuildable)

    def _build_sidebar(self) -> None:
        # Rebuildable after rooms are added/removed/renamed: tear down the
        # previous sidebar and reset the per-build widget bookkeeping.
        existing = getattr(self, "_sidebar_frame", None)
        if existing is not None:
            existing.destroy()
        self._room_buttons = []
        self._room_button_index = {}
        self._city_groups = []
        self._selected_button = None

        sidebar = ctk.CTkFrame(
            self._room_panel, width=SIDEBAR_WIDTH, corner_radius=0, fg_color=COLORS["sidebar"],
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(2, weight=1)  # room list expands
        sidebar.grid_columnconfigure(0, weight=1)
        self._sidebar_frame = sidebar

        # "ROOMS" header + count, with an Add Room (+) button.
        rooms_header = ctk.CTkFrame(sidebar, fg_color="transparent")
        rooms_header.grid(row=0, column=0, sticky="ew", padx=PAD - 2, pady=(PAD - 4, 6))
        rooms_header.grid_columnconfigure(0, weight=1)
        self._rooms_label = ctk.CTkLabel(
            rooms_header, text=f"ROOMS  ({len(self.site.rooms)})", anchor="w",
            font=font(11, weight="bold"), text_color=COLORS["text_faint"],
        )
        self._rooms_label.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            rooms_header, text="＋", width=28, height=24, corner_radius=CORNER,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"], text_color=COLORS["text_muted"],
            font=font(13), command=self.add_room,
        ).grid(row=0, column=1, sticky="e")

        # Room filter (Ctrl+F focuses it from anywhere).
        self._sidebar_search_entry = ctk.CTkEntry(
            sidebar, textvariable=self._search_var, placeholder_text="Filter rooms…",
            height=32, corner_radius=CORNER, border_width=1,
            border_color=COLORS["border"], fg_color=COLORS["card"],
        )
        self._sidebar_search_entry.grid(
            row=1, column=0, sticky="ew", padx=PAD - 2, pady=(0, 8))

        # Scrollable room list, grouped into collapsible city boxes.
        room_list = ctk.CTkScrollableFrame(sidebar, fg_color="transparent", corner_radius=0)
        room_list.grid(row=2, column=0, sticky="nsew", padx=(PAD - 8, PAD - 10))
        room_list.grid_columnconfigure(0, weight=1)

        if self.site.is_multi_city:
            for index, (city, rooms) in enumerate(self.site.grouped_by_city().items()):
                group = CityGroup(room_list, city, rooms, self)
                group.grid(row=index, column=0, sticky="ew", pady=(0, 6))
                self._city_groups.append(group)
            if self._city_groups:
                self._city_groups[0].set_collapsed(False)  # expand the first city
        else:
            for index, room in enumerate(self.site.rooms):
                btn = RoomButton(room_list, room, command=self.select_room)
                btn.grid(row=index, column=0, sticky="ew", pady=3)
                self._room_buttons.append(btn)
                self._room_button_index[room.id] = btn

        # Footer: config source.
        source = self.config_path.name if self.config_path else "example data"
        self._config_footer_label = ctk.CTkLabel(
            sidebar, text=f"config: {source}   ·   v{__version__}", anchor="w",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._config_footer_label.grid(row=3, column=0, sticky="ew", padx=PAD - 2, pady=(6, PAD - 4))

    def _build_room_detail(self) -> None:
        detail = ctk.CTkFrame(self._room_panel, corner_radius=0, fg_color=COLORS["panel"])
        detail.grid(row=0, column=1, sticky="nsew")
        detail.grid_columnconfigure(0, weight=1)
        detail.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(detail, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=PAD + 4, pady=(PAD, 4))
        head.grid_columnconfigure(0, weight=1)
        self._room_title = ctk.CTkLabel(
            head, text="", anchor="w",
            font=font(20, weight="bold"), text_color=COLORS["text"],
        )
        self._room_title.grid(row=0, column=0, sticky="w")

        # Status filter chips — scope the card grid to all/online/offline.
        chips = ctk.CTkFrame(head, fg_color="transparent")
        chips.grid(row=0, column=1, rowspan=3, sticky="e", padx=(GAP, 0))
        self._filter_chips: dict[str, ctk.CTkButton] = {}
        for key, label in (("all", "ALL"), ("online", "ONLINE"), ("offline", "OFFLINE")):
            chip = ctk.CTkButton(
                chips, text=label, width=20, height=26,
                font=font(10, mono=True),
                command=lambda k=key: self.set_room_filter(k),
                **BTN_GHOST,
            )
            chip.pack(side="left", padx=(0, 6))
            self._filter_chips[key] = chip
        self._paint_filter_chips()
        self._room_subtitle = ctk.CTkLabel(
            head, text="", anchor="w",
            font=font(12), text_color=COLORS["text_muted"],
        )
        self._room_subtitle.grid(row=1, column=0, sticky="w", pady=(1, 0))
        self._room_metrics = ctk.CTkFrame(head, fg_color="transparent")
        self._room_metrics.grid(row=2, column=0, sticky="w", pady=(8, 0))
        self._metric_cells: list[tuple[ctk.CTkLabel, ctk.CTkLabel]] = []
        for _index, (_val, _cap) in enumerate([
            ("—", "24H UPTIME"), ("—", "ONLINE"), ("—", "OFFLINE"),
            ("—", "WEB UIS"), ("—", "AVG LATENCY"),
        ]):
            _cell = ctk.CTkFrame(self._room_metrics, fg_color="transparent")
            _cell.grid(row=0, column=_index, sticky="w", padx=(0, 22))
            _v = ctk.CTkLabel(
                _cell, text=_val, anchor="w",
                font=font(15, mono=True, weight="bold"),
                text_color=COLORS["text"],
            )
            _v.pack(anchor="w")
            _c = ctk.CTkLabel(
                _cell, text=_cap, anchor="w",
                font=font(9), text_color=COLORS["text_faint"],
            )
            _c.pack(anchor="w")
            self._metric_cells.append((_v, _c))

        self._grid = ctk.CTkScrollableFrame(detail, fg_color="transparent")
        self._grid.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=(GAP, 0))
        for col in range(GRID_COLUMNS):
            self._grid.grid_columnconfigure(col, weight=1, uniform="cards")

        self._statusbar = ctk.CTkLabel(
            detail, text="", anchor="w", height=26,
            font=font(12), text_color=COLORS["text_faint"],
        )
        self._statusbar.grid(row=2, column=0, sticky="ew", padx=PAD + 8, pady=(0, 8))

    # ------------------------------------------------------------------ #
    # Room selection + rendering
    # ------------------------------------------------------------------ #
    def select_room(self, room: Room) -> None:
        # Choosing a room always leaves the dashboard for the room view, even
        # when it's the room already loaded underneath the overview.
        self.show_room_view()
        if room is self.current_room:
            return
        self.current_room = room
        # Highlight the matching sidebar button if it has been built yet; if its
        # city group is still collapsed/unbuilt, register_room_buttons() will
        # apply the highlight when the group is expanded.
        target = next((b for b in self._room_buttons if b.room is room), None)
        if target is None and self._city_groups:
            # Programmatic selection of a room in a collapsed group: reveal it.
            self._select_in_sidebar(room)
        else:
            self._mark_selected(target)
        self._render_room(room)

    def _mark_selected(self, button: RoomButton | None) -> None:
        """Move the selection highlight to ``button`` (O(1))."""

        if self._selected_button is button:
            return
        if self._selected_button is not None:
            self._selected_button.set_selected(False)
        if button is not None:
            button.set_selected(True)
        self._selected_button = button

    def register_room_buttons(self, buttons: list[RoomButton]) -> None:
        """Called by a CityGroup once it lazily builds its room buttons."""

        self._room_buttons.extend(buttons)
        for btn in buttons:
            self._room_button_index[btn.room.id] = btn
        # If the currently-selected room's button was just built, highlight it.
        if self.current_room is not None and self._selected_button is None:
            for btn in buttons:
                if btn.room is self.current_room:
                    self._mark_selected(btn)
                    break

    def _render_room(self, room: Room) -> None:
        # Header text + breadcrumb.
        self._room_title.configure(text=room.name)
        bits = [b for b in (room.city, room.location, f"{room.device_count} devices") if b]
        self._room_subtitle.configure(text="   ·   ".join(bits))
        self._render_room_metrics(room)
        if self._current_view == "rooms":
            self._update_topbar()

        # A room is selected: room-scoped editing actions are available.
        self._edit_room_btn.configure(state="normal")
        self._add_device_btn.configure(state="normal")
        self._check_btn.configure(state="normal")

        # Web-UI action: reflect how many devices are openable in this room.
        web_count = len(room.web_devices())
        if web_count:
            self._open_btn.configure(text=f"Open Web UIs ({web_count})", state="normal")
        else:
            self._open_btn.configure(text="Open Web UIs", state="disabled")

        self._device_cards.clear()

        # Empty room: guide the user straight to adding their first device.
        hint = self._get_empty_hint()
        if room.device_count == 0:
            hint.grid(row=0, column=0, columnspan=GRID_COLUMNS, sticky="ew", pady=GAP)
        else:
            hint.grid_remove()

        # Lay out using pooled widgets — rebind content instead of recreating,
        # so a room switch is O(devices) cheap reconfigures, not widget builds.
        card_idx = 0
        section_idx = 0
        row = 0
        shown = 0
        for category, devices in room.devices_by_category().items():
            devices = [d for d in devices if self._passes_room_filter(d)]
            if not devices:
                continue
            shown += len(devices)
            section = self._get_section(section_idx)
            section_idx += 1
            section.configure(text=f"{category}   ({len(devices)})")
            section.grid(
                row=row, column=0, columnspan=GRID_COLUMNS,
                sticky="w", pady=(GAP if row else 0, 6),
            )
            row += 1

            col = 0
            for device in devices:
                card = self._get_card(card_idx)
                card_idx += 1
                card.set_device(device)
                card.grid(row=row, column=col, sticky="ew", padx=6, pady=6)
                self._device_cards[device.id] = card
                col += 1
                if col >= GRID_COLUMNS:
                    col = 0
                    row += 1
            if col != 0:
                row += 1

        # Hide any pooled widgets not needed by this (smaller) room.
        for card in self._card_pool[card_idx:]:
            card.grid_remove()
        for section in self._section_pool[section_idx:]:
            section.grid_remove()

        # An active filter with no matches deserves a message, not a blank grid.
        if room.device_count and not shown:
            empty = self._get_filter_empty_label()
            empty.grid(row=0, column=0, columnspan=GRID_COLUMNS, sticky="w", pady=GAP)
        else:
            empty = getattr(self, "_filter_empty_label", None)
            if empty is not None:
                empty.grid_remove()

        self._update_statusbar()

    def _passes_room_filter(self, device: Device) -> bool:
        if self._room_filter == "online":
            return device.status is DeviceStatus.ONLINE
        if self._room_filter == "offline":
            return device.status is DeviceStatus.OFFLINE
        return True

    def set_room_filter(self, key: str) -> None:
        """Scope the room's card grid to all/online/offline devices."""

        if key == self._room_filter:
            return
        self._room_filter = key
        self._paint_filter_chips()
        if self.current_room is not None:
            self._render_room(self.current_room)

    def _paint_filter_chips(self) -> None:
        for key, chip in self._filter_chips.items():
            if key == self._room_filter:
                # Active scope reads as orientation azure, not a stark white block.
                chip.configure(
                    fg_color=COLORS["accent2_soft"], text_color=COLORS["accent2_text"],
                    border_color=COLORS["accent2_line"],
                )
            else:
                chip.configure(
                    fg_color="transparent", text_color=COLORS["text_muted"],
                    border_color=COLORS["border"],
                )

    def _get_filter_empty_label(self) -> ctk.CTkLabel:
        label = getattr(self, "_filter_empty_label", None)
        if label is None:
            label = ctk.CTkLabel(
                self._grid, text="No devices match this filter.", anchor="w",
                font=font(13), text_color=COLORS["text_faint"],
            )
            self._filter_empty_label = label
        return label

    def _get_card(self, index: int) -> DeviceCard:
        """Return pooled card ``index``, creating it on first use."""

        while index >= len(self._card_pool):
            card = DeviceCard(self._grid)
            card._click_cb = self._open_device_controls
            self._card_pool.append(card)
        return self._card_pool[index]

    def _get_section(self, index: int) -> ctk.CTkLabel:
        """Return pooled section header ``index``, creating it on first use."""

        while index >= len(self._section_pool):
            self._section_pool.append(
                ctk.CTkLabel(
                    self._grid, text="", anchor="w",
                    font=font(13, weight="bold"),
                    text_color=COLORS["text_muted"],
                )
            )
        return self._section_pool[index]

    def _get_empty_hint(self) -> ctk.CTkFrame:
        """The 'this room has no devices yet' placeholder (built once)."""

        hint = getattr(self, "_empty_hint", None)
        if hint is None:
            hint = ctk.CTkFrame(
                self._grid, fg_color=COLORS["card"], corner_radius=CORNER,
                border_width=1, border_color=COLORS["border"],
            )
            ctk.CTkLabel(
                hint, text="No devices in this room yet.",
                font=font(14, weight="bold"), text_color=COLORS["text"],
            ).pack(anchor="w", padx=PAD, pady=(PAD, 2))
            ctk.CTkLabel(
                hint, text="Add the AV equipment installed here to monitor and control it.",
                font=font(12), text_color=COLORS["text_muted"],
            ).pack(anchor="w", padx=PAD)
            ctk.CTkButton(
                hint, text="+ ADD A DEVICE", height=40,
                font=font(11, mono=True, weight="bold"), command=self.add_device,
                **BTN_SOLID,
            ).pack(anchor="w", padx=PAD, pady=PAD)
            self._empty_hint = hint
        return hint

    def _render_room_metrics(self, room: Room) -> None:
        """Repaint the room-head metric strip (uptime / web UIs / latency / subnet)."""

        online = sum(1 for d in room.devices if d.status is DeviceStatus.ONLINE)
        offline = sum(1 for d in room.devices if d.status is DeviceStatus.OFFLINE)
        web_count = len(room.web_devices())
        lats = [d.last_latency_ms for d in room.devices if d.last_latency_ms is not None]
        avg_lat = f"{int(sum(lats) / len(lats))} ms" if lats else "—"
        uptime = self.history.room_uptime(room.id, 24 * 3600)
        up_text = f"{uptime:.1f}%" if uptime is not None else "—"
        up_color = (
            COLORS["online"] if (uptime or 0) >= 99 else
            COLORS["warn"] if uptime is not None else COLORS["text_faint"]
        )
        updates = [
            (up_text,        up_color),
            (str(online),    COLORS["online"]  if online  else COLORS["text_muted"]),
            (str(offline),   COLORS["offline"] if offline else COLORS["text_muted"]),
            (str(web_count), COLORS["text"]),
            (avg_lat,        COLORS["text"]),
        ]
        for (value_lbl, _cap_lbl), (value, color) in zip(self._metric_cells, updates):
            value_lbl.configure(text=value, text_color=color)

    def _on_search_changed(self) -> None:
        """Search typed: navigate to Rooms immediately, then debounce the filter."""

        if self._search_var.get().strip() and self._current_view != "rooms":
            self.navigate("rooms")
        if self._search_debounce_job is not None:
            self.after_cancel(self._search_debounce_job)
        self._search_debounce_job = self.after(250, self._fire_search)

    def _fire_search(self) -> None:
        self._search_debounce_job = None
        self._filter_rooms()

    def _filter_rooms(self) -> None:
        """Filter sidebar rooms by the search box (name/city/location/id)."""

        if not getattr(self, "_sidebar_frame", None):
            return
        query = self._search_var.get().strip().lower()
        if self._city_groups:
            visible = sum(group.apply_filter(query) for group in self._city_groups)
        else:
            visible = 0
            for btn in self._room_buttons:
                if not query or query in btn._search_haystack:
                    btn.grid()
                    visible += 1
                else:
                    btn.grid_remove()

        total = len(self._room_buttons)
        label = getattr(self, "_rooms_label", None)
        if label is not None:
            label.configure(text=f"ROOMS  ({visible}/{total})" if query else f"ROOMS  ({total})")

    def _show_empty_state(self) -> None:
        self._room_title.configure(text="No rooms configured")
        self._room_subtitle.configure(
            text="Use “＋ Add” in the sidebar to create your first room."
        )
        # Nothing to act on: dim every room-scoped action.
        for btn in (self._open_btn, self._check_btn,
                    self._add_device_btn, self._edit_room_btn):
            btn.configure(state="disabled")
        # Clear any device widgets left over from a previously-selected room.
        self._device_cards.clear()
        for card in self._card_pool:
            card.grid_remove()
        for section in self._section_pool:
            section.grid_remove()
        hint = getattr(self, "_empty_hint", None)
        if hint is not None:
            hint.grid_remove()
        self._update_statusbar()

    # ------------------------------------------------------------------ #
    # View switching
    # ------------------------------------------------------------------ #
    def show_dashboard(self) -> None:
        """Reveal the estate-wide overview (kept for callers/back-compat)."""

        self.navigate("overview")

    def show_room_view(self) -> None:
        """Reveal the per-room device view."""

        self.navigate("rooms")

    def open_room_from_dashboard(self, room: Room) -> None:
        """Jump from a dashboard tile/row straight into that room's view."""

        self.select_room(room)  # select_room → show_room_view → navigate("rooms")

    def _update_nav_highlight(self, old_view: str | None = None) -> None:
        tabs = getattr(self, "_nav_tabs", None)
        if tabs:
            # Only reconfigure tabs whose state actually changed.
            keys_to_update: set[str] = {self._current_view}
            if old_view is not None:
                keys_to_update.add(old_view)
            for key in keys_to_update:
                tab = tabs.get(key)
                if tab is not None:
                    tab.set_active(key == self._current_view)
        # The room-selection highlight belongs to the room view only. Off the
        # room view no room is "active", so visually clear it (keeping
        # ``_selected_button`` so the highlight is restored on the way back).
        if self._selected_button is not None:
            self._selected_button.set_selected(self._current_view == "rooms")

    def _refresh_dashboard(self) -> None:
        """Repaint the dashboard if it exists (cheap no-op when never opened)."""

        if self._dashboard is not None:
            self._dashboard.refresh()

    # ------------------------------------------------------------------ #
    # Keyboard shortcuts + command palette
    # ------------------------------------------------------------------ #
    def _bind_shortcuts(self) -> None:
        """Global shortcuts, bound on the root so they fire from any widget."""

        self.bind("<Control-k>", self._kb(self.open_palette))
        self.bind("<Control-p>", self._kb(self.open_palette))
        self.bind("<Control-f>", self._kb(self._focus_room_filter))
        self.bind("<Control-comma>", self._kb(self.open_settings))
        self.bind("<F5>", self._kb(self._refresh_current_view))
        self.bind("<F1>", self._kb(self.open_shortcuts))
        for index, (key, _label) in enumerate(NAV_ITEMS, start=1):
            self.bind(f"<Control-Key-{index}>", self._kb(lambda k=key: self.navigate(k)))

    @staticmethod
    def _kb(action):
        """Wrap an action as a Tk event handler that consumes the keystroke."""

        def handler(_event=None):
            action()
            return "break"

        return handler

    def open_palette(self) -> None:
        """Open the Ctrl+K command palette (one at a time)."""

        if self._palette is not None and self._palette.winfo_exists():
            return
        self._palette = CommandPalette(self)

    def open_device(self, room: Room, device: Device) -> None:
        """Jump to ``room`` and open ``device``'s control dialog (palette path)."""

        self.select_room(room)
        DeviceControlDialog(self, device)

    def open_folder(self, path: Path) -> None:
        """Reveal a folder in the platform file manager (for diagnostics)."""

        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as exc:
            logger.warning("Could not open folder %s: %s", path, exc)
            messagebox.showerror(__app_name__, f"Could not open the folder:\n\n{path}")

    def _focus_room_filter(self) -> None:
        self.navigate("rooms")
        entry = getattr(self, "_sidebar_search_entry", None)
        if entry is not None:
            entry.focus_set()

    def _refresh_current_view(self) -> None:
        """F5: re-probe whatever is on screen (room check or estate sweep)."""

        if self._current_view == "overview":
            self.run_estate_sweep()
        elif self._current_view == "rooms":
            self.on_check_status()

    def _update_attention_badge(self) -> None:
        """Sync the nav bar's offline-count badge with the live estate state."""

        badge = getattr(self, "_attention_badge", None)
        if badge is None:
            return
        offline = sum(
            1 for d in self.site.all_devices() if d.status is DeviceStatus.OFFLINE
        )
        if offline:
            badge.configure(text=f"●  {offline} OFFLINE")
            if not badge.winfo_manager():
                badge.pack(side="left", before=self._palette_btn)
        elif badge.winfo_manager():
            badge.pack_forget()

    # ------------------------------------------------------------------ #
    # Estate-wide status sweep (feeds the dashboard + uptime history)
    # ------------------------------------------------------------------ #
    def run_estate_sweep(self) -> None:
        """Probe every device in every room concurrently, off the UI thread.

        Mirrors :meth:`on_check_status` but spans the whole site. Results update
        each device's live status (so the dashboard KPIs, sidebar dots and any
        open room repaint) and are persisted to the uptime history on completion.
        """

        if self._sweeping:
            return
        devices = list(self.site.all_devices())
        if not devices:
            self._set_statusbar("No devices to check.")
            return

        self._sweep_gen += 1
        generation = self._sweep_gen
        self._sweeping = True
        self._sweep_total = len(devices)
        self._sweep_done = 0
        self._sweep_samples = []
        self._sweep_index = {
            device.id: (room, device)
            for room in self.site.rooms
            for device in room.devices
        }
        for device in devices:
            device.status = DeviceStatus.CHECKING
        self._repaint_after_sweep_change()
        if self._dashboard is not None:
            self._dashboard.set_sweeping(True)

        timeout = self._effective_timeout()
        concurrency = self._effective_concurrency()
        sweep_queue = self._sweep_queue
        logger.info(
            "Estate sweep started (%d device(s), timeout %.1fs, max %d concurrent)",
            len(devices), timeout, concurrency,
        )

        def publish(result: CheckResult) -> None:
            sweep_queue.put(("result", generation, result))

        def worker() -> None:
            try:
                run_status_checks(devices, timeout, publish, concurrency)
            finally:
                sweep_queue.put(("done", generation))

        threading.Thread(target=worker, daemon=True).start()
        self.after(self._poll_interval_ms, self._drain_sweep)

    def _drain_sweep(self) -> None:
        while True:
            try:
                message = self._sweep_queue.get_nowait()
            except queue.Empty:
                break
            if message[0] == "result":
                _, generation, result = message
                self._apply_sweep_result(generation, result)
            elif message[0] == "done":
                self._finish_sweep(message[1])
        if self._sweeping:
            # Live progress readout — once per tick, not once per device.
            self._ov_sweep_label.configure(
                text=f"sweeping {self._sweep_done}/{self._sweep_total}…"
            )
            self.after(self._poll_interval_ms, self._drain_sweep)

    def _apply_sweep_result(self, generation: int, result: CheckResult) -> None:
        if generation != self._sweep_gen:
            return  # superseded
        entry = self._sweep_index.get(result.device_id)
        if entry is None:
            return
        room, device = entry
        device.status = result.status
        device.last_latency_ms = result.latency_ms
        device.last_error = result.error
        self._sweep_done += 1
        self._sweep_samples.append(
            Sample(device.id, room.id, result.status, result.latency_ms)
        )
        # Online recorders carry a second state worth surfacing on the dashboard.
        if isinstance(device, Recorder):
            if result.status is DeviceStatus.ONLINE and device.recording_status_url:
                self._poll_recording(device, room)
            else:
                device.recording_status = RecordingStatus.UNKNOWN

    def _finish_sweep(self, generation: int) -> None:
        if generation != self._sweep_gen:
            return
        self._sweeping = False
        self.last_sweep_time = datetime.now()
        # One pass over the estate for the tallies *and* the offline set (it
        # can be thousands of devices; iterating repeatedly is pure waste).
        online = offline = 0
        offline_devices: list[tuple[str, str]] = []  # (device id, name)
        for device in self.site.all_devices():
            if device.status is DeviceStatus.ONLINE:
                online += 1
            elif device.status is DeviceStatus.OFFLINE:
                offline += 1
                offline_devices.append((device.id, device.name))
        logger.info("Estate sweep complete: %d online, %d offline", online, offline)
        audit("status_check.estate", devices=len(self._sweep_index), online=online, offline=offline)
        self._notify_sweep_result(online, offline, offline_devices)
        self._repaint_after_sweep_change()
        if self._dashboard is not None:
            self._dashboard.set_sweeping(False)
            self._dashboard.refresh()
        self._refresh_dashboards_board()
        # Persist the batch off the UI thread (SQLite write is best-effort).
        samples = self._sweep_samples
        self._sweep_samples = []
        if samples:
            self.run_background(lambda: self.history.record(samples), lambda ok, msg: None)

    def _notify_sweep_result(
        self, online: int, offline: int, offline_devices: list[tuple[str, str]]
    ) -> None:
        """Toast the sweep outcome; call out devices that *newly* went offline."""

        previous = self._last_offline_ids
        self._last_offline_ids = {device_id for device_id, _ in offline_devices}

        new_names = (
            [name for device_id, name in offline_devices if device_id not in previous]
            if previous is not None else []
        )
        if new_names:
            shown = ", ".join(new_names[:3])
            if len(new_names) > 3:
                shown += f"  (+{len(new_names) - 3} more)"
            self.toaster.show(
                f"{len(new_names)} device(s) went offline", shown,
                kind="error", on_click=lambda: self.navigate("overview"),
            )
        elif offline:
            self.toaster.show(
                f"Sweep complete — {offline} device(s) still offline",
                f"{online} online across {len(self.site.rooms)} rooms",
                kind="warn", on_click=lambda: self.navigate("overview"),
            )
        else:
            self.toaster.show(
                "Sweep complete — estate healthy",
                f"All {online} checked devices are online",
                kind="success",
            )

    def _repaint_after_sweep_change(self) -> None:
        """Refresh sidebar dots and the visible room/dashboard after a bulk change."""

        for btn in self._room_buttons:
            btn.refresh_health()
        if self.current_room is not None:
            if self._room_filter != "all":
                # Membership of the filtered grid may have changed; re-lay it out.
                self._render_room(self.current_room)
            else:
                for card in self._device_cards.values():
                    card.refresh()
                self._update_statusbar()
        if self._showing_dashboard:
            self._refresh_dashboard()
        self._update_attention_badge()

    # ------------------------------------------------------------------ #
    # Status indicator API (Step 4 ping worker calls these)
    # ------------------------------------------------------------------ #
    def set_device_status(self, device: Device, status: DeviceStatus) -> None:
        """Update one device's status and repaint its card. UI-thread only."""

        device.status = status
        card = self._device_cards.get(device.id)
        if card is not None:
            card.refresh()
        self._update_statusbar()

    def _refresh_room_health(self, room: Room) -> None:
        """Update the sidebar dot for *room* to reflect its current health."""

        btn = self._room_button_index.get(room.id)
        if btn is not None:
            btn.refresh_health()

    def set_room_status(self, status: DeviceStatus) -> None:
        """Bulk-set every device in the current room to a status."""

        if not self.current_room:
            return
        for device in self.current_room.devices:
            device.status = status
        for card in self._device_cards.values():
            card.refresh()
        self._update_statusbar()
        self._refresh_room_health(self.current_room)

    def _update_statusbar(self) -> None:
        if self._statusbar_clear_job:
            self.after_cancel(self._statusbar_clear_job)
            self._statusbar_clear_job = None
        if not self.current_room:
            self._statusbar.configure(text="")
            return
        counts: dict[DeviceStatus, int] = {}
        for device in self.current_room.devices:
            counts[device.status] = counts.get(device.status, 0) + 1
        online = counts.get(DeviceStatus.ONLINE, 0)
        offline = counts.get(DeviceStatus.OFFLINE, 0)
        checking = counts.get(DeviceStatus.CHECKING, 0)
        total = self.current_room.device_count
        text = f"{total} devices   ·   {online} online   ·   {offline} offline"
        if checking:
            text += f"   ·   checking {checking}…"
        self._statusbar.configure(text=text)

    def _set_statusbar(self, text: str, duration_ms: int = 5000) -> None:
        if self._statusbar_clear_job:
            self.after_cancel(self._statusbar_clear_job)
            self._statusbar_clear_job = None
        self._statusbar.configure(text=text)
        if duration_ms > 0:
            self._statusbar_clear_job = self.after(duration_ms, self._update_statusbar)

    # ------------------------------------------------------------------ #
    # Actions
    # ------------------------------------------------------------------ #
    def on_open_web_uis(self) -> None:
        """Open every web-accessible device in the current room in the browser.

        Re-creates the legacy tool's headline behaviour. Devices without a web
        UI (raw TCP / SSH only) are skipped. Above a threshold we confirm first
        so a dense room can't accidentally spawn dozens of tabs.
        """

        if not self.current_room:
            return
        urls = self.current_room.web_urls()
        if not urls:
            self._set_statusbar("No web-accessible devices in this room.")
            return

        if len(urls) > self._web_confirm_threshold:
            if not messagebox.askyesno(
                __app_name__,
                f"Open {len(urls)} web UIs for '{self.current_room.name}' "
                f"in your browser?",
            ):
                return

        opened = open_urls(urls, self.browser_cfg)
        where = "browser"
        if self.browser_cfg.path:
            where = Path(self.browser_cfg.path).stem or "browser"
        elif self.browser_cfg.name:
            where = self.browser_cfg.name
        logger.info("Opening %d web UI(s) for room '%s'", opened, self.current_room.name)
        audit(
            "room.open_web_uis",
            room_id=self.current_room.id,
            room_name=self.current_room.name,
            count=opened,
            browser=where,
        )
        self._set_statusbar(
            f"Opening {opened} web UI(s) for '{self.current_room.name}' in {where}…"
        )

    def on_check_status(self) -> None:
        """Concurrently probe every device in the current room, live.

        The probing runs on a background thread (so the UI never freezes);
        each result is marshalled back onto the Tk thread to flip its card
        green/red as it arrives.
        """

        if not self.current_room or self._checking:
            return
        room = self.current_room
        devices = list(room.devices)
        if not devices:
            self._set_statusbar("No devices to check in this room.")
            return

        # New run: bump the generation so any stragglers from a prior run are
        # ignored, and flip everything to CHECKING up front.
        self._check_gen += 1
        generation = self._check_gen
        self._checking = True
        self._checking_room = room
        self._checking_room_index = {d.id: d for d in devices}
        self._check_btn.configure(state="disabled", text="CHECKING…")
        self.set_room_status(DeviceStatus.CHECKING)

        timeout = self._effective_timeout()
        concurrency = self._effective_concurrency()
        result_queue = self._result_queue
        logger.info(
            "Status check started for room '%s' (%d device(s), timeout %.1fs)",
            room.name, len(devices), timeout,
        )

        def publish(result: CheckResult) -> None:
            # Runs on the worker thread: only touch the thread-safe queue here.
            result_queue.put(("result", generation, result))

        def worker() -> None:
            try:
                run_status_checks(devices, timeout, publish, concurrency)
            finally:
                result_queue.put(("done", generation))

        threading.Thread(target=worker, daemon=True).start()
        # Poll the queue from the Tk thread (Tk calls must stay on this thread).
        self.after(self._poll_interval_ms, self._drain_results)

    def _drain_results(self) -> None:
        """Apply queued probe results on the Tk thread; reschedule until done.

        Results are applied to the model as a batch and the room is repainted
        **once** per tick — not once per device — so a check of a dense room
        stays smooth instead of paying a full statusbar recount and sidebar
        refresh for every individual result.
        """

        changed: list[Device] = []
        done_generation: int | None = None
        while True:
            try:
                message = self._result_queue.get_nowait()
            except queue.Empty:
                break
            if message[0] == "result":
                _, generation, result = message
                device = self._apply_result(generation, result)
                if device is not None:
                    changed.append(device)
            elif message[0] == "done":
                done_generation = message[1]

        if changed and self.current_room is self._checking_room:
            for device in changed:
                card = self._device_cards.get(device.id)
                if card is not None and card.device is device:
                    card.refresh()
            self._update_statusbar()
            self._update_attention_badge()
            if self._checking_room is not None:
                self._refresh_room_health(self._checking_room)

        if done_generation is not None:
            self._finish_check(done_generation)

        if self._checking:
            self.after(self._poll_interval_ms, self._drain_results)

    def _apply_result(self, generation: int, result: CheckResult) -> Device | None:
        """Apply one probe result to the model; return the device if it changed.

        Repainting is handled in bulk by :meth:`_drain_results`; this only
        touches the data model so it stays cheap to call per result.
        """

        if generation != self._check_gen or self._checking_room is None:
            return None  # superseded by a newer run
        room = self._checking_room
        device = self._checking_room_index.get(result.device_id)
        if device is None:
            return None
        device.status = result.status
        device.last_latency_ms = result.latency_ms
        device.last_error = result.error
        # Recorders carry a second, independent state (are they recording?).
        # Poll it only when the device is reachable; otherwise it's unknown.
        if isinstance(device, Recorder):
            if result.status is DeviceStatus.ONLINE and device.recording_status_url:
                self._poll_recording(device, room)
            else:
                device.recording_status = RecordingStatus.UNKNOWN
        return device

    def _poll_recording(self, device: Recorder, room: Room) -> None:
        """Fetch a recorder's recording state off-thread and repaint its card.

        Runs after the reachability probe says the device is online. Failures
        are swallowed by :func:`fetch_recording_status` (returns UNKNOWN), so
        ``on_done`` always lands with ``ok=True``.
        """

        url = device.recording_status_url
        if not url:
            return
        json_path = device.recording_status_json_path
        timeout = self._effective_timeout()

        def on_done(ok: bool, status) -> None:
            if not ok or not isinstance(status, RecordingStatus):
                return
            device.recording_status = status
            if self.current_room is room:
                card = self._device_cards.get(device.id)
                if card is not None and card.device is device:
                    card.refresh()

        self.run_background(lambda: fetch_recording_status(url, json_path, timeout), on_done)

    def _finish_check(self, generation: int) -> None:
        if generation != self._check_gen:
            return
        self._checking = False
        self._checking_room_index = {}
        self._check_btn.configure(state="normal", text="CHECK STATUS")
        self._update_statusbar()
        self._update_attention_badge()
        room = self._checking_room
        if room:
            self._refresh_room_health(room)
            online = sum(1 for d in room.devices if d.status is DeviceStatus.ONLINE)
            offline = sum(1 for d in room.devices if d.status is DeviceStatus.OFFLINE)
            logger.info(
                "Status check complete for room '%s': %d online, %d offline",
                room.name, online, offline,
            )
            audit(
                "status_check.complete",
                room_id=room.id,
                room_name=room.name,
                devices=len(room.devices),
                online=online,
                offline=offline,
            )
            # Feed the uptime history so manual room checks build trend data too.
            samples = [
                Sample(d.id, room.id, d.status, d.last_latency_ms)
                for d in room.devices
                if d.status.is_resolved
            ]
            if samples:
                self.run_background(lambda: self.history.record(samples), lambda ok, msg: None)
            # An active status filter must re-evaluate membership now that the
            # final statuses are in.
            if self._room_filter != "all" and self.current_room is room:
                self._render_room(room)
            # If the overview / custom board is open behind a check, keep it current.
            if self._showing_dashboard:
                self._refresh_dashboard()
            self._refresh_dashboards_board()

    # ------------------------------------------------------------------ #
    # Effective settings (state overrides config)
    # ------------------------------------------------------------------ #
    def _effective_timeout(self) -> float:
        if self.app_state.ping_timeout_seconds:
            return float(self.app_state.ping_timeout_seconds)
        return self.site.ping_timeout_seconds

    def _effective_concurrency(self) -> int:
        """Max simultaneous probes: user preference > config > built-in default.

        Caps how many connections a status check / estate sweep opens at once
        so a large estate never floods the network or exhausts sockets.
        """

        if self.app_state.max_concurrent_checks > 0:
            return int(self.app_state.max_concurrent_checks)
        if self.site.max_concurrent_checks > 0:
            return self.site.max_concurrent_checks
        return DEFAULT_MAX_CONCURRENCY

    def _effective_browser_cfg(self) -> BrowserConfig:
        cfg = BrowserConfig.from_settings(self.site.settings)
        if self.app_state.browser_path:
            cfg.path = self.app_state.browser_path
        cfg.new_window = bool(self.app_state.browser_new_window)
        return cfg

    def apply_settings(self) -> None:
        """Re-read preferences after the Settings dialog saves."""

        self.browser_cfg = self._effective_browser_cfg()
        self._reschedule_auto_refresh()
        self._reschedule_dashboard_poll()

    # ------------------------------------------------------------------ #
    # Generic background runner (for one-shot device control commands)
    # ------------------------------------------------------------------ #
    def run_background(self, work, on_done) -> None:
        """Run ``work()`` on a thread; call ``on_done(ok: bool, message: str)``.

        ``on_done`` always runs on the Tk thread. Used by device control
        commands so a slow/unreachable device never freezes the UI.
        """

        result_queue: "queue.Queue[tuple]" = queue.Queue()

        def worker() -> None:
            try:
                result_queue.put((True, work()))
            except Exception as exc:  # surface any failure to the dialog
                # Full traceback to the diagnostic log; concise text to the UI.
                logger.warning(
                    "Background task failed: %s\n%s", exc, traceback.format_exc()
                )
                result_queue.put((False, str(exc)))

        threading.Thread(target=worker, daemon=True).start()

        def poll() -> None:
            try:
                ok, message = result_queue.get_nowait()
            except queue.Empty:
                self.after(self._poll_interval_ms, poll)
                return
            on_done(ok, message)

        self.after(self._poll_interval_ms, poll)

    # ------------------------------------------------------------------ #
    # Dialogs / config switching
    # ------------------------------------------------------------------ #
    def _open_device_controls(self, device: Device) -> None:
        DeviceControlDialog(self, device)

    def open_live_view(self, room: Room | None, device: Device) -> None:
        """Pop out (or retarget) the single live-video window for ``device``.

        One window and one ffmpeg decode app-wide: opening a second camera
        swaps the feed in the existing window instead of stacking video
        surfaces on Tk.
        """

        if not device.stream_url:
            return
        if room is None:
            room = next((r for r in self.site.rooms if device in r.devices), None)
        if room is None:
            return
        if find_ffmpeg() is None:
            audit("stream.open", device_id=device.id, device_name=device.name,
                  ok=False, error="ffmpeg not found")
            messagebox.showerror(
                __app_name__,
                "Live view needs ffmpeg to decode the camera stream.\n\n"
                "Install ffmpeg and put it on PATH, place ffmpeg.exe next to "
                "mission-deck.exe, or point the MISSION_DECK_FFMPEG "
                "environment variable at the binary.",
            )
            return
        if self._live_view is not None and self._live_view.winfo_exists():
            self._live_view.show_device(room, device)
            self._live_view.lift()
            self._live_view.focus_force()
        else:
            self._live_view = LiveViewWindow(self, room, device)

    def open_settings(self) -> None:
        SettingsDialog(self)

    def open_shortcuts(self) -> None:
        ShortcutsDialog(self)

    # ------------------------------------------------------------------ #
    # Window geometry persistence
    # ------------------------------------------------------------------ #
    def _restore_window_geometry(self) -> None:
        """Re-open at the size/position (or maximised state) of last session."""

        saved = self.app_state.window_geometry
        if not saved:
            return
        if saved == "zoomed":
            # Maximising must wait until the window is mapped on some WMs.
            self.after(0, lambda: self._try_zoom())
        elif re.fullmatch(r"\d{3,5}x\d{3,5}[+-]\d+[+-]\d+", saved):
            self.geometry(saved)

    def _try_zoom(self) -> None:
        try:
            self.state("zoomed")
        except Exception:  # not supported on this platform/WM — cosmetic
            logger.debug("Could not restore maximised state", exc_info=True)

    def destroy(self) -> None:
        """Capture the window geometry before tearing down (saved by main())."""

        # Stop the live-view ffmpeg process explicitly: a cascaded Tk teardown
        # destroys the Toplevel at C level without running its close() path.
        if self._live_view is not None and self._live_view.winfo_exists():
            try:
                self._live_view.close()
            except Exception:
                logger.debug("Could not close live view cleanly", exc_info=True)
        try:
            if self.state() == "zoomed":
                self.app_state.window_geometry = "zoomed"
            else:
                self.app_state.window_geometry = self.geometry()
        except Exception:
            pass
        super().destroy()

    # ------------------------------------------------------------------ #
    # Config editing (add/remove/edit rooms, devices, commands)
    # ------------------------------------------------------------------ #
    def persist_config(self) -> bool:
        """Write the current in-memory site back to the config file.

        When viewing demo data (no config path yet), prompts for a location to
        save to first — turning the demo into the user's own editable config.
        Returns ``True`` on success.
        """

        if self.config_path is None:
            chosen = filedialog.asksaveasfilename(
                title="Save configuration as…",
                defaultextension=".json",
                initialfile="config.json",
                filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
            )
            if not chosen:
                return False
            self.config_path = Path(chosen)

        try:
            save_config(self.config_path, self.site.to_dict())
        except (ConfigError, OSError) as exc:
            logger.error("Failed to save config to %s: %s", self.config_path, exc)
            audit("config.save", path=str(self.config_path), ok=False, error=str(exc))
            messagebox.showerror(
                __app_name__, f"Could not save your configuration:\n\n{exc}"
            )
            return False

        audit(
            "config.save",
            path=str(self.config_path),
            ok=True,
            rooms=len(self.site.rooms),
        )
        self.app_state.remember_config(self.config_path)
        self.app_state.save()
        self._refresh_config_footer()
        return True

    def add_room(self) -> None:
        RoomEditorDialog(self)

    def edit_current_room(self) -> None:
        if self.current_room is not None:
            RoomEditorDialog(self, self.current_room)

    def add_device(self) -> None:
        if self.current_room is not None:
            DeviceEditorDialog(self, self.current_room)

    def refresh_sidebar(self) -> None:
        """Rebuild the room list (after add/remove/rename) and restore selection."""

        self._build_sidebar()
        if self.current_room is not None and self.current_room in self.site.rooms:
            self._select_in_sidebar(self.current_room)

    def _select_in_sidebar(self, room: Room) -> None:
        """Expand the room's city group (if any) and highlight its button."""

        for group in self._city_groups:
            if room in group.rooms:
                group.set_collapsed(False)  # builds the buttons
                break
        target = next((b for b in self._room_buttons if b.room is room), None)
        if target is not None:
            self._mark_selected(target)

    def refresh_current_room(self) -> None:
        """Re-render the current room after its devices changed."""

        if self.current_room is not None:
            self._render_room(self.current_room)
            self._refresh_room_health(self.current_room)

    def select_first_or_empty(self) -> None:
        """After a deletion, fall back to the first room or the empty state."""

        self.current_room = None
        self._selected_button = None
        if self.site.rooms:
            self.select_room(self.site.rooms[0])
        else:
            self._show_empty_state()

    def _refresh_config_footer(self) -> None:
        if getattr(self, "_config_footer_label", None) is not None:
            source = self.config_path.name if self.config_path else "example data"
            self._config_footer_label.configure(text=f"config: {source}")

    def switch_config_dialog(self) -> None:
        """Pick a different config and soft-restart onto it (see ``main``)."""

        chosen = filedialog.askopenfilename(
            title="Open a mission-deck configuration",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
        )
        if chosen:
            logger.info("User requested config switch to %s", chosen)
            audit("config.switch", to=str(Path(chosen)), frm=str(self.config_path) if self.config_path else None)
            self.requested_config = Path(chosen)
            self.destroy()

    # ------------------------------------------------------------------ #
    # Auto-refresh
    # ------------------------------------------------------------------ #
    def on_toggle_auto_refresh(self) -> None:
        self.app_state.auto_refresh_enabled = bool(self._auto_var.get())
        self.app_state.save()
        self._reschedule_auto_refresh()
        if self.app_state.auto_refresh_enabled:
            secs = self.app_state.auto_refresh_seconds or 60
            self._set_statusbar(f"Auto-refresh every {secs}s is on.")

    def _reschedule_auto_refresh(self) -> None:
        if self._auto_refresh_job is not None:
            self.after_cancel(self._auto_refresh_job)
            self._auto_refresh_job = None
        if self.app_state.auto_refresh_enabled and (self.app_state.auto_refresh_seconds or 0) > 0:
            interval_ms = int(self.app_state.auto_refresh_seconds * 1000)
            self._auto_refresh_job = self.after(interval_ms, self._auto_refresh_tick)

    def _auto_refresh_tick(self) -> None:
        self._auto_refresh_job = None
        if not self._checking and self.current_room is not None:
            self.on_check_status()
        self._reschedule_auto_refresh()

    # ------------------------------------------------------------------ #
    # Dashboard background poll (estate-wide sweep on a timer)
    # ------------------------------------------------------------------ #
    def on_toggle_dashboard_poll(self, enabled: bool) -> None:
        self.app_state.dashboard_poll_enabled = bool(enabled)
        self.app_state.save()
        self._reschedule_dashboard_poll()
        if enabled:
            secs = self.app_state.dashboard_poll_seconds or 120
            self._set_statusbar(f"Background refresh every {secs}s is on.")
            # Kick off an immediate sweep so the dashboard isn't blank until the
            # first interval elapses.
            self.run_estate_sweep()

    def _reschedule_dashboard_poll(self) -> None:
        if self._dashboard_poll_job is not None:
            self.after_cancel(self._dashboard_poll_job)
            self._dashboard_poll_job = None
        if self.app_state.dashboard_poll_enabled and (self.app_state.dashboard_poll_seconds or 0) > 0:
            interval_ms = int(self.app_state.dashboard_poll_seconds * 1000)
            self._dashboard_poll_job = self.after(interval_ms, self._dashboard_poll_tick)

    def _dashboard_poll_tick(self) -> None:
        self._dashboard_poll_job = None
        if not self._sweeping:
            self.run_estate_sweep()
        self._reschedule_dashboard_poll()


# --------------------------------------------------------------------------- #
# Welcome screen (friendly first-run / no-config experience)
# --------------------------------------------------------------------------- #
class WelcomeWindow(ctk.CTk):
    """A friendly chooser shown when no config can be auto-loaded.

    Far gentler than a bare OS file dialog: it explains what's needed and offers
    big buttons to open a file, reuse a recent config, or explore demo data.
    Sets ``result`` to one of ``("file", path)`` / ``("demo", None)`` /
    ``("quit", None)``.
    """

    def __init__(self, state: AppState):
        super().__init__()
        self.app_state = state
        self.result: tuple[str, Path | None] = ("quit", None)

        ctk.set_appearance_mode(state.appearance or "dark")
        self.title(f"{__app_name__}  ·  Welcome")
        self.geometry("560x460")
        self.configure(fg_color=COLORS["bg"])
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text="mission-deck", font=font(28, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w", padx=PAD + 12, pady=(PAD + 12, 0))
        ctk.CTkLabel(
            self, text="Courtroom AV Manager", font=font(14),
            text_color=COLORS["text_muted"],
        ).grid(row=1, column=0, sticky="w", padx=PAD + 12)
        ctk.CTkLabel(
            self,
            text="To get started, open your configuration file. It lists the\n"
                 "rooms and devices for your sites. No file yet? Explore the demo.",
            justify="left", font=font(13), text_color=COLORS["text_muted"],
        ).grid(row=2, column=0, sticky="w", padx=PAD + 12, pady=(GAP, PAD))

        ctk.CTkButton(
            self, text="OPEN CONFIGURATION FILE…", height=48,
            font=font(13, mono=True, weight="bold"), command=self._open_file,
            **BTN_SOLID,
        ).grid(row=3, column=0, sticky="ew", padx=PAD + 12, pady=6)
        ctk.CTkButton(
            self, text="EXPLORE DEMO DATA", height=44,
            font=font(12, mono=True), command=self._use_demo,
            **BTN_OUTLINE,
        ).grid(row=4, column=0, sticky="ew", padx=PAD + 12, pady=6)

        # Recent configs (if any still exist on disk).
        recents = [Path(p) for p in state.recent_configs if Path(p).is_file()]
        if recents:
            ctk.CTkLabel(
                self, text="RECENT", anchor="w",
                font=font(11, weight="bold"), text_color=COLORS["text_faint"],
            ).grid(row=5, column=0, sticky="ew", padx=PAD + 12, pady=(PAD, 2))
            recent_box = ctk.CTkScrollableFrame(self, fg_color="transparent", height=120)
            recent_box.grid(row=6, column=0, sticky="nsew", padx=PAD + 8)
            recent_box.grid_columnconfigure(0, weight=1)
            self.grid_rowconfigure(6, weight=1)
            for index, path in enumerate(recents):
                ctk.CTkButton(
                    recent_box, text=f"  {path.name}   —   {path.parent}", anchor="w",
                    height=34, corner_radius=CORNER, fg_color="transparent",
                    hover_color=COLORS["card_hover"], text_color=COLORS["text_muted"],
                    font=font(12),
                    command=lambda p=path: self._choose(("file", p)),
                ).grid(row=index, column=0, sticky="ew", pady=2)

    def _choose(self, result: tuple[str, Path | None]) -> None:
        self.result = result
        self.destroy()

    def _open_file(self) -> None:
        chosen = filedialog.askopenfilename(
            title="Select a mission-deck configuration",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
        )
        if chosen:
            self._choose(("file", Path(chosen)))

    def _use_demo(self) -> None:
        self._choose(("demo", None))


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _resolve_startup_choice(state: AppState) -> tuple[str, Path | None]:
    """Decide what to load on launch, prompting via the welcome screen if needed."""

    # 1. The config the user opened last time — zero prompts for the common case.
    if state.last_config_path:
        last = Path(state.last_config_path)
        if last.is_file():
            return ("file", last)
    # 2. A config discovered in the standard locations.
    found = find_config()
    if found is not None:
        return ("file", found)
    # 3. Ask, nicely.
    welcome = WelcomeWindow(state)
    welcome.mainloop()
    return welcome.result


def _run(state: AppState) -> None:
    """The startup/soft-restart loop. Separated so :func:`main` can wrap it with
    logging setup and a top-level safety net."""

    pending: Path | None = None  # set when the user switches config (soft restart)

    while True:
        if pending is not None:
            kind, path = "file", pending
            pending = None
        else:
            kind, path = _resolve_startup_choice(state)

        if kind == "quit":
            logger.info("User chose to quit at startup")
            return

        try:
            if kind == "demo":
                site = Site.from_loaded_config(load_config(example_config_path()))
                path = None
            else:
                site = Site.from_loaded_config(load_config(path))
        except (ConfigError, DeviceConfigError, OSError) as exc:
            # ConfigError      -> file missing / unreadable / bad JSON / bad shape
            # DeviceConfigError -> structurally OK but a room/device is invalid
            logger.error("Could not load configuration (%s): %s", type(exc).__name__, exc)
            audit("config.load", path=str(path) if path else None, ok=False, error=str(exc))
            messagebox.showerror(
                __app_name__, f"Could not load configuration:\n\n{exc}"
            )
            # Force the welcome screen next time rather than looping on a bad file.
            state.last_config_path = None
            continue

        audit(
            "config.load",
            path=str(path) if path else None,
            demo=path is None,
            rooms=len(site.rooms),
            ok=True,
        )
        if path is not None:
            state.remember_config(path)
            state.save()

        app = App(site, config_path=path, state=state)
        app.mainloop()
        state.save()
        app.history.close()

        if app.requested_config is not None:
            pending = app.requested_config
            continue
        return


def main() -> None:
    log_location = setup_logging()
    logger.info("mission-deck %s starting", __version__)
    audit("app.start", version=__version__, log_dir=str(log_location) if log_location else None)
    state = AppState.load()
    try:
        _run(state)
    except Exception:  # last-resort safety net for an otherwise-fatal crash
        logger.critical("Fatal error; the application must close", exc_info=True)
        detail = traceback.format_exc()
        where = f"\n\nDetails were written to the log{f' at {log_location}' if log_location else ''}."
        try:
            messagebox.showerror(
                __app_name__,
                "mission-deck hit an unexpected error and needs to close."
                f"{where}\n\n{detail.strip().splitlines()[-1]}",
            )
        except Exception:
            pass  # GUI may already be torn down; the log is the record that matters.
        raise
    finally:
        logger.info("mission-deck exiting")
        audit("app.stop")


if __name__ == "__main__":
    main()
