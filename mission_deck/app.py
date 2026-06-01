"""mission-deck desktop UI (CustomTkinter).

The visual shell — a dark, enterprise-style window with a room sidebar (grouped
into collapsible city boxes) and a main panel rendering the selected room's
devices as a grid of cards grouped by category.

Beyond browsing it provides:
  * a live async **status check** (cards flip green/red without freezing the UI),
  * optional **auto-refresh** of the current room,
  * **Open Web UIs** to launch every web device in a room, and
  * per-device **control actions** (click a card) driven by config commands.

It is also built for non-technical users: the last-opened config is remembered,
a friendly **welcome screen** replaces a bare file dialog, and a **Settings**
dialog exposes every preference (browser, timeouts, appearance) so nobody has
to edit JSON by hand.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from mission_deck import __app_name__, __version__
from mission_deck.browser import BrowserConfig, open_urls
from mission_deck.controls import DeviceControl, controls_for
from mission_deck.network import CheckResult, run_status_checks
from mission_deck.config import (
    ConfigError,
    example_config_path,
    find_config,
    load_config,
)
from mission_deck.models import Device, DeviceStatus, Room, Site
from mission_deck.state import AppState
from mission_deck.theme import (
    CORNER,
    GAP,
    GRID_COLUMNS,
    PAD,
    SIDEBAR_WIDTH,
    COLORS,
    status_color,
    status_label,
)


# --------------------------------------------------------------------------- #
# Sidebar room button
# --------------------------------------------------------------------------- #
class RoomButton(ctk.CTkButton):
    """A selectable room entry in the sidebar."""

    # Fonts are shared across all instances so selecting a room never builds a
    # new font object — important when there are 100+ buttons.
    _font_normal: ctk.CTkFont | None = None
    _font_bold: ctk.CTkFont | None = None

    def __init__(self, master, room: Room, command):
        self.room = room
        cls = type(self)
        if cls._font_normal is None:
            cls._font_normal = ctk.CTkFont(size=14, weight="normal")
            cls._font_bold = ctk.CTkFont(size=14, weight="bold")
        super().__init__(
            master,
            text=f"  {room.name}",
            anchor="w",
            height=44,
            corner_radius=CORNER,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            text_color=COLORS["text_muted"],
            font=cls._font_normal,
            command=lambda: command(room),
        )

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.configure(
                fg_color=COLORS["accent_soft"],
                text_color=COLORS["text"],
                font=type(self)._font_bold,
            )
        else:
            self.configure(
                fg_color="transparent",
                text_color=COLORS["text_muted"],
                font=type(self)._font_normal,
            )


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

        # Layout: [status dot] [text block .................]
        self.grid_columnconfigure(1, weight=1)

        # Status indicator (a crisp coloured bullet we can recolour live).
        self._dot = ctk.CTkLabel(
            self, text="●", width=18, font=ctk.CTkFont(size=18),
            text_color=status_color(DeviceStatus.UNKNOWN),
        )
        self._dot.grid(row=0, column=0, rowspan=3, padx=(14, 8), pady=14, sticky="n")

        self._name = ctk.CTkLabel(
            self, text="", anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=COLORS["text"],
        )
        self._name.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(14, 0))

        self._subtitle = ctk.CTkLabel(
            self, text="", anchor="w",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
        )
        self._subtitle.grid(row=1, column=1, sticky="ew", padx=(0, 14))

        self._meta = ctk.CTkLabel(
            self, text="", anchor="w",
            font=ctk.CTkFont(size=11, family="Consolas"), text_color=COLORS["text_faint"],
        )
        self._meta.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=(0, 14))

        # Hover affordance + click to open the device's control actions.
        for widget in (self, self._dot, self._name, self._subtitle, self._meta):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)
            widget.bind("<Button-1>", self._on_click)
            widget.configure(cursor="hand2")

    def set_device(self, device: Device) -> None:
        """Rebind this (pooled) card to display ``device``."""

        self.device = device
        subtitle = device.description
        if device.category not in subtitle:
            subtitle = f"{device.category} · {subtitle}" if subtitle else device.category
        self._name.configure(text=device.name)
        self._subtitle.configure(text=subtitle)
        self.refresh()

    def _meta_text(self) -> str:
        assert self.device is not None
        proto = self.device.protocol.upper()
        web = "  🌐" if self.device.is_web_accessible else ""
        return (
            f"{self.device.address}   {proto}   ·   "
            f"{status_label(self.device.status)}{web}"
        )

    def refresh(self) -> None:
        """Repaint status-dependent bits (indicator colour + meta line)."""

        if self.device is None:
            return
        self._dot.configure(text_color=status_color(self.device.status))
        self._meta.configure(text=self._meta_text())

    def _on_enter(self, _event=None) -> None:
        self.configure(fg_color=COLORS["card_hover"])

    def _on_leave(self, _event=None) -> None:
        self.configure(fg_color=COLORS["card"])

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
            height=34,
            corner_radius=CORNER,
            fg_color=COLORS["header"],
            hover_color=COLORS["card_hover"],
            text_color=COLORS["text_muted"],
            font=ctk.CTkFont(size=12, weight="bold"),
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
        self._header.configure(text=f"  {chevron}  {self.city}   ({count})")

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
            match = query in self._haystack(btn.room)
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
        self.configure(fg_color=COLORS["bg"])
        self.geometry("420x360")
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=device.name, anchor="w",
            font=ctk.CTkFont(size=18, weight="bold"), text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 0))
        ctk.CTkLabel(
            self, text=f"{device.category} · {device.description}", anchor="w",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
        ).grid(row=1, column=0, sticky="ew", padx=PAD)
        ctk.CTkLabel(
            self, text=f"{device.address}   {device.protocol.upper()}", anchor="w",
            font=ctk.CTkFont(size=11, family="Consolas"), text_color=COLORS["text_faint"],
        ).grid(row=2, column=0, sticky="ew", padx=PAD, pady=(0, GAP))

        actions = ctk.CTkScrollableFrame(self, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="nsew", padx=PAD - 4, pady=0)
        actions.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        controls = controls_for(device, app.browser_cfg)
        if not controls:
            ctk.CTkLabel(
                actions,
                text="No control actions configured for this device.\n"
                     "Add a \"commands\" list to it in your config to add buttons.",
                anchor="w", justify="left",
                font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
            ).grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        for index, control in enumerate(controls):
            is_web = control.kind == "web"
            ctk.CTkButton(
                actions,
                text=control.label,
                height=40,
                corner_radius=CORNER,
                anchor="w",
                fg_color=COLORS["accent"] if is_web else COLORS["card"],
                hover_color=COLORS["accent_hover"] if is_web else COLORS["card_hover"],
                border_width=0 if is_web else 1,
                border_color=COLORS["border"],
                font=ctk.CTkFont(size=13, weight="bold"),
                command=lambda c=control: self._run_control(c),
            ).grid(row=index, column=0, sticky="ew", padx=8, pady=4)

        self._status = ctk.CTkLabel(
            self, text="", anchor="w",
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
        )
        self._status.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(GAP, PAD))

        self.after(60, self._focus)

    def _focus(self) -> None:
        try:
            self.lift()
            self.grab_set()
            self.focus_force()
        except Exception:
            pass

    def _run_control(self, control: DeviceControl) -> None:
        value = None
        if control.prompt:
            dialog = ctk.CTkInputDialog(text=control.prompt, title=control.label)
            value = dialog.get_input()
            if value is None:  # cancelled
                return
        self._status.configure(text=f"Running “{control.label}”…", text_color=COLORS["text_muted"])

        def on_done(ok: bool, message: str) -> None:
            self._status.configure(
                text=message,
                text_color=COLORS["online"] if ok else COLORS["offline"],
            )

        self.app.run_background(lambda: control.run(value), on_done)


# --------------------------------------------------------------------------- #
# Settings dialog
# --------------------------------------------------------------------------- #
class SettingsDialog(ctk.CTkToplevel):
    """GUI for every preference, so users never edit JSON by hand."""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self.app_state = app.app_state
        self.title("Settings")
        self.configure(fg_color=COLORS["bg"])
        self.geometry("520x460")
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=PAD)
        body.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        row = 0

        def label(text: str) -> None:
            nonlocal row
            ctk.CTkLabel(
                body, text=text, anchor="w",
                font=ctk.CTkFont(size=13), text_color=COLORS["text"],
            ).grid(row=row, column=0, sticky="w", padx=(0, GAP), pady=8)

        # Appearance
        label("Appearance")
        self._appearance = ctk.StringVar(value=self.app_state.appearance or "dark")
        ctk.CTkOptionMenu(
            body, values=["dark", "light", "system"], variable=self._appearance,
            command=lambda v: ctk.set_appearance_mode(v),
            fg_color=COLORS["card"], button_color=COLORS["card_hover"],
        ).grid(row=row, column=1, sticky="ew", pady=8); row += 1

        # Ping timeout
        label("Status check timeout (seconds)")
        self._timeout = ctk.StringVar(value=str(app._effective_timeout()))
        ctk.CTkEntry(body, textvariable=self._timeout, fg_color=COLORS["card"]).grid(
            row=row, column=1, sticky="ew", pady=8); row += 1

        # Auto-refresh interval
        label("Auto-refresh interval (seconds)")
        self._refresh = ctk.StringVar(value=str(self.app_state.auto_refresh_seconds or 60))
        ctk.CTkEntry(body, textvariable=self._refresh, fg_color=COLORS["card"]).grid(
            row=row, column=1, sticky="ew", pady=8); row += 1

        # Browser path + browse
        label("Browser (leave blank for system default)")
        browser_row = ctk.CTkFrame(body, fg_color="transparent")
        browser_row.grid(row=row, column=1, sticky="ew", pady=8)
        browser_row.grid_columnconfigure(0, weight=1)
        self._browser = ctk.StringVar(value=self.app_state.browser_path)
        ctk.CTkEntry(browser_row, textvariable=self._browser, fg_color=COLORS["card"]).grid(
            row=0, column=0, sticky="ew")
        ctk.CTkButton(
            browser_row, text="Browse…", width=80, command=self._browse_browser,
            fg_color=COLORS["card"], hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"],
        ).grid(row=0, column=1, padx=(GAP, 0)); row += 1

        # Browser new window
        label("Open web UIs in a new window")
        self._new_window = ctk.BooleanVar(value=self.app_state.browser_new_window)
        ctk.CTkSwitch(body, text="", variable=self._new_window).grid(
            row=row, column=1, sticky="w", pady=8); row += 1

        # Config file switch
        label("Configuration file")
        ctk.CTkButton(
            body, text="Open a different config…", command=self._switch_config,
            fg_color=COLORS["card"], hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"],
        ).grid(row=row, column=1, sticky="ew", pady=8); row += 1

        # Buttons
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(0, PAD))
        buttons.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(
            buttons, text="Cancel", width=100, command=self.destroy,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"],
        ).grid(row=0, column=1, padx=(0, GAP))
        ctk.CTkButton(
            buttons, text="Save", width=100, command=self._save,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
        ).grid(row=0, column=2)

        self.after(60, lambda: (self.lift(), self.grab_set(), self.focus_force()))

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

        self.app_state.appearance = self._appearance.get()
        self.app_state.ping_timeout_seconds = timeout
        self.app_state.auto_refresh_seconds = refresh
        self.app_state.browser_path = self._browser.get().strip()
        self.app_state.browser_new_window = bool(self._new_window.get())
        self.app_state.save()

        ctk.set_appearance_mode(self.app_state.appearance)
        self.app.apply_settings()
        self.destroy()


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
        # NB: ``app_state`` (not ``state``) — Tk reserves ``state()`` as a method.
        self.app_state = state if state is not None else AppState.load()
        self.browser_cfg = self._effective_browser_cfg()
        self.current_room: Room | None = None
        self._room_buttons: list[RoomButton] = []
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
        self._result_queue: "queue.Queue[tuple]" = queue.Queue()
        self._poll_interval_ms = 40
        # Auto-refresh + config-switch state.
        self._auto_refresh_job: str | None = None
        self.requested_config: Path | None = None

        # --- Window chrome -------------------------------------------------- #
        ctk.set_appearance_mode(self.app_state.appearance or "dark")
        ctk.set_default_color_theme("blue")
        self.title(f"{__app_name__}  ·  AV Room Manager")
        self.geometry("1200x760")
        self.minsize(1000, 640)
        self.configure(fg_color=COLORS["bg"])

        self.grid_columnconfigure(0, weight=0)  # sidebar (fixed)
        self.grid_columnconfigure(1, weight=1)  # main panel (stretch)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_panel()

        # Select the first room so the layout is populated on launch.
        if self.site.rooms:
            self.select_room(self.site.rooms[0])
        else:
            self._show_empty_state()

        # Honour a saved auto-refresh preference.
        self._reschedule_auto_refresh()

    # ------------------------------------------------------------------ #
    # Sidebar
    # ------------------------------------------------------------------ #
    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self,
            width=SIDEBAR_WIDTH,
            corner_radius=0,
            fg_color=COLORS["sidebar"],
        )
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(3, weight=1)  # room list expands

        # Brand / title block.
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD + 4, 4))
        ctk.CTkLabel(
            brand,
            text="mission-deck",
            anchor="w",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text="Courtroom AV Manager",
            anchor="w",
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_faint"],
        ).pack(anchor="w")

        # "ROOMS" section label + count.
        self._rooms_label = ctk.CTkLabel(
            sidebar,
            text=f"ROOMS  ({len(self.site.rooms)})",
            anchor="w",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=COLORS["text_faint"],
        )
        self._rooms_label.grid(row=1, column=0, sticky="ew", padx=PAD + 4, pady=(PAD, 6))

        # Search/filter box — essential once there are ~115 rooms.
        self._search_var = ctk.StringVar()
        self._search_var.trace_add("write", lambda *_: self._filter_rooms())
        search = ctk.CTkEntry(
            sidebar,
            textvariable=self._search_var,
            placeholder_text="Search rooms…",
            height=34,
            corner_radius=CORNER,
            border_width=1,
            border_color=COLORS["border"],
            fg_color=COLORS["card"],
        )
        search.grid(row=2, column=0, sticky="ew", padx=PAD, pady=(0, 8))

        # Scrollable room list, grouped into collapsible city boxes.
        room_list = ctk.CTkScrollableFrame(
            sidebar, fg_color="transparent", corner_radius=0
        )
        room_list.grid(row=3, column=0, sticky="nsew", padx=PAD - 6)
        room_list.grid_columnconfigure(0, weight=1)

        if self.site.is_multi_city:
            # One collapsible box per city (Melbourne, Sydney, …). Buttons are
            # built lazily when a group is expanded — see CityGroup.
            for index, (city, rooms) in enumerate(self.site.grouped_by_city().items()):
                group = CityGroup(room_list, city, rooms, self)
                group.grid(row=index, column=0, sticky="ew", pady=(0, 6))
                self._city_groups.append(group)
            if self._city_groups:
                self._city_groups[0].set_collapsed(False)  # expand the first city
        else:
            # No city info anywhere: flat list, no headers.
            for index, room in enumerate(self.site.rooms):
                btn = RoomButton(room_list, room, command=self.select_room)
                btn.grid(row=index, column=0, sticky="ew", pady=3)
                self._room_buttons.append(btn)

        # Footer: config source + version.
        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=PAD, pady=(6, PAD))
        source = self.config_path.name if self.config_path else "example data"
        ctk.CTkLabel(
            footer,
            text=f"config: {source}",
            anchor="w",
            font=ctk.CTkFont(size=11, family="Consolas"),
            text_color=COLORS["text_faint"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            footer,
            text=f"v{__version__}",
            anchor="w",
            font=ctk.CTkFont(size=11),
            text_color=COLORS["text_faint"],
        ).pack(anchor="w")

    # ------------------------------------------------------------------ #
    # Main panel
    # ------------------------------------------------------------------ #
    def _build_main_panel(self) -> None:
        panel = ctk.CTkFrame(self, corner_radius=0, fg_color=COLORS["panel"])
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        # --- Header bar: room title + actions ------------------------------ #
        header = ctk.CTkFrame(panel, corner_radius=0, fg_color=COLORS["header"], height=88)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.grid_columnconfigure(0, weight=1)

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.grid(row=0, column=0, sticky="w", padx=PAD + 8, pady=PAD)
        self._room_title = ctk.CTkLabel(
            title_block,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=COLORS["text"],
        )
        self._room_title.pack(anchor="w")
        self._room_subtitle = ctk.CTkLabel(
            title_block,
            text="",
            anchor="w",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_muted"],
        )
        self._room_subtitle.pack(anchor="w")

        actions = ctk.CTkFrame(header, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e", padx=PAD + 8, pady=PAD)
        # Primary action (rightmost): live status check.
        self._check_btn = ctk.CTkButton(
            actions,
            text="Check Status",
            width=140,
            height=40,
            corner_radius=CORNER,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.on_check_status,
        )
        self._check_btn.pack(side="right")
        # Headline feature: open every web UI in the room (secondary style).
        self._open_btn = ctk.CTkButton(
            actions,
            text="Open Web UIs",
            width=150,
            height=40,
            corner_radius=CORNER,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self.on_open_web_uis,
        )
        self._open_btn.pack(side="right", padx=(0, GAP))
        # Settings gear (far left of the action cluster).
        self._settings_btn = ctk.CTkButton(
            actions,
            text="⚙",
            width=40,
            height=40,
            corner_radius=CORNER,
            fg_color="transparent",
            hover_color=COLORS["card_hover"],
            border_width=1,
            border_color=COLORS["border"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16),
            command=self.open_settings,
        )
        self._settings_btn.pack(side="right", padx=(0, GAP))
        # Auto-refresh toggle.
        self._auto_var = ctk.BooleanVar(value=self.app_state.auto_refresh_enabled)
        self._auto_switch = ctk.CTkSwitch(
            actions,
            text="Auto",
            variable=self._auto_var,
            command=self.on_toggle_auto_refresh,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_muted"],
        )
        self._auto_switch.pack(side="right", padx=(0, GAP + 4))

        # --- Scrollable device grid ---------------------------------------- #
        self._grid = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        self._grid.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=PAD)
        for col in range(GRID_COLUMNS):
            self._grid.grid_columnconfigure(col, weight=1, uniform="cards")

        # --- Status / toast line ------------------------------------------- #
        self._statusbar = ctk.CTkLabel(
            panel,
            text="",
            anchor="w",
            height=28,
            font=ctk.CTkFont(size=12),
            text_color=COLORS["text_faint"],
        )
        self._statusbar.grid(row=2, column=0, sticky="ew", padx=PAD + 8, pady=(0, 8))

    # ------------------------------------------------------------------ #
    # Room selection + rendering
    # ------------------------------------------------------------------ #
    def select_room(self, room: Room) -> None:
        if room is self.current_room:
            return
        self.current_room = room
        # Highlight the matching sidebar button if it has been built yet; if its
        # city group is still collapsed/unbuilt, register_room_buttons() will
        # apply the highlight when the group is expanded.
        target = next((b for b in self._room_buttons if b.room is room), None)
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
        # If the currently-selected room's button was just built, highlight it.
        if self.current_room is not None and self._selected_button is None:
            for btn in buttons:
                if btn.room is self.current_room:
                    self._mark_selected(btn)
                    break

    def _render_room(self, room: Room) -> None:
        # Header text.
        self._room_title.configure(text=room.name)
        bits = [b for b in (room.location, f"{room.device_count} devices") if b]
        self._room_subtitle.configure(text="   ·   ".join(bits))

        # Web-UI action: reflect how many devices are openable in this room.
        web_count = len(room.web_devices())
        if web_count:
            self._open_btn.configure(text=f"Open Web UIs  ({web_count})", state="normal")
        else:
            self._open_btn.configure(text="Open Web UIs", state="disabled")

        self._device_cards.clear()

        # Lay out using pooled widgets — rebind content instead of recreating,
        # so a room switch is O(devices) cheap reconfigures, not widget builds.
        card_idx = 0
        section_idx = 0
        row = 0
        for category, devices in room.devices_by_category().items():
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

        self._update_statusbar()

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
                    font=ctk.CTkFont(size=13, weight="bold"),
                    text_color=COLORS["text_muted"],
                )
            )
        return self._section_pool[index]

    def _filter_rooms(self) -> None:
        """Filter sidebar rooms by the search box (name/city/location/id)."""

        query = self._search_var.get().strip().lower()
        if self._city_groups:
            visible = sum(group.apply_filter(query) for group in self._city_groups)
        else:
            visible = 0
            for btn in self._room_buttons:
                room = btn.room
                haystack = f"{room.name} {room.location} {room.id}".lower()
                if not query or query in haystack:
                    btn.grid()
                    visible += 1
                else:
                    btn.grid_remove()

        total = len(self._room_buttons)
        if query:
            self._rooms_label.configure(text=f"ROOMS  ({visible}/{total})")
        else:
            self._rooms_label.configure(text=f"ROOMS  ({total})")

    def _show_empty_state(self) -> None:
        self._room_title.configure(text="No rooms configured")
        self._room_subtitle.configure(
            text="Your config.json contains no rooms."
        )

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

    def set_room_status(self, status: DeviceStatus) -> None:
        """Bulk-set every device in the current room to a status."""

        if not self.current_room:
            return
        for device in self.current_room.devices:
            device.status = status
        for card in self._device_cards.values():
            card.refresh()
        self._update_statusbar()

    def _update_statusbar(self) -> None:
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
            self._statusbar.configure(
                text="No web-accessible devices in this room."
            )
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
        self._statusbar.configure(
            text=f"Opening {opened} web UI(s) for '{self.current_room.name}' in {where}…"
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
            self._statusbar.configure(text="No devices to check in this room.")
            return

        # New run: bump the generation so any stragglers from a prior run are
        # ignored, and flip everything to CHECKING up front.
        self._check_gen += 1
        generation = self._check_gen
        self._checking = True
        self._checking_room = room
        self._check_btn.configure(state="disabled", text="Checking…")
        self.set_room_status(DeviceStatus.CHECKING)

        timeout = self._effective_timeout()
        result_queue = self._result_queue

        def publish(result: CheckResult) -> None:
            # Runs on the worker thread: only touch the thread-safe queue here.
            result_queue.put(("result", generation, result))

        def worker() -> None:
            try:
                run_status_checks(devices, timeout, publish)
            finally:
                result_queue.put(("done", generation))

        threading.Thread(target=worker, daemon=True).start()
        # Poll the queue from the Tk thread (Tk calls must stay on this thread).
        self.after(self._poll_interval_ms, self._drain_results)

    def _drain_results(self) -> None:
        """Apply queued probe results on the Tk thread; reschedule until done."""

        while True:
            try:
                message = self._result_queue.get_nowait()
            except queue.Empty:
                break
            if message[0] == "result":
                _, generation, result = message
                self._apply_result(generation, result)
            elif message[0] == "done":
                self._finish_check(message[1])

        if self._checking:
            self.after(self._poll_interval_ms, self._drain_results)

    def _apply_result(self, generation: int, result: CheckResult) -> None:
        """Apply one probe result on the UI thread."""

        if generation != self._check_gen or self._checking_room is None:
            return  # superseded by a newer run
        room = self._checking_room
        device = room.get_device(result.device_id)
        if device is None:
            return
        device.status = result.status
        device.last_latency_ms = result.latency_ms
        device.last_error = result.error
        # Repaint only if this device's card is the one currently on screen.
        if self.current_room is room:
            card = self._device_cards.get(device.id)
            if card is not None and card.device is device:
                card.refresh()
            self._update_statusbar()

    def _finish_check(self, generation: int) -> None:
        if generation != self._check_gen:
            return
        self._checking = False
        self._check_btn.configure(state="normal", text="Check Status")
        self._update_statusbar()

    # ------------------------------------------------------------------ #
    # Effective settings (state overrides config)
    # ------------------------------------------------------------------ #
    def _effective_timeout(self) -> float:
        if self.app_state.ping_timeout_seconds:
            return float(self.app_state.ping_timeout_seconds)
        return self.site.ping_timeout_seconds

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

    def open_settings(self) -> None:
        SettingsDialog(self)

    def switch_config_dialog(self) -> None:
        """Pick a different config and soft-restart onto it (see ``main``)."""

        chosen = filedialog.askopenfilename(
            title="Open a mission-deck configuration",
            filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
        )
        if chosen:
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
            self._statusbar.configure(text=f"Auto-refresh every {secs}s is on.")

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
            self, text="mission-deck", font=ctk.CTkFont(size=28, weight="bold"),
            text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w", padx=PAD + 12, pady=(PAD + 12, 0))
        ctk.CTkLabel(
            self, text="Courtroom AV Manager", font=ctk.CTkFont(size=14),
            text_color=COLORS["text_muted"],
        ).grid(row=1, column=0, sticky="w", padx=PAD + 12)
        ctk.CTkLabel(
            self,
            text="To get started, open your configuration file. It lists the\n"
                 "rooms and devices for your sites. No file yet? Explore the demo.",
            justify="left", font=ctk.CTkFont(size=13), text_color=COLORS["text_muted"],
        ).grid(row=2, column=0, sticky="w", padx=PAD + 12, pady=(GAP, PAD))

        ctk.CTkButton(
            self, text="Open Configuration File…", height=48, corner_radius=CORNER,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=15, weight="bold"), command=self._open_file,
        ).grid(row=3, column=0, sticky="ew", padx=PAD + 12, pady=6)
        ctk.CTkButton(
            self, text="Explore Demo Data", height=44, corner_radius=CORNER,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"], text_color=COLORS["text"],
            font=ctk.CTkFont(size=14), command=self._use_demo,
        ).grid(row=4, column=0, sticky="ew", padx=PAD + 12, pady=6)

        # Recent configs (if any still exist on disk).
        recents = [Path(p) for p in state.recent_configs if Path(p).is_file()]
        if recents:
            ctk.CTkLabel(
                self, text="RECENT", anchor="w",
                font=ctk.CTkFont(size=11, weight="bold"), text_color=COLORS["text_faint"],
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
                    font=ctk.CTkFont(size=12),
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


def main() -> None:
    state = AppState.load()
    pending: Path | None = None  # set when the user switches config (soft restart)

    while True:
        if pending is not None:
            kind, path = "file", pending
            pending = None
        else:
            kind, path = _resolve_startup_choice(state)

        if kind == "quit":
            return

        try:
            if kind == "demo":
                site = Site.from_loaded_config(load_config(example_config_path()))
                path = None
            else:
                site = Site.from_loaded_config(load_config(path))
        except (ConfigError, OSError) as exc:
            messagebox.showerror(
                __app_name__, f"Could not load configuration:\n\n{exc}"
            )
            # Force the welcome screen next time rather than looping on a bad file.
            state.last_config_path = None
            continue

        if path is not None:
            state.remember_config(path)
            state.save()

        app = App(site, config_path=path, state=state)
        app.mainloop()
        state.save()

        if app.requested_config is not None:
            pending = app.requested_config
            continue
        return


if __name__ == "__main__":
    main()
