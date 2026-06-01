"""mission-deck desktop UI (CustomTkinter).

Step 3 deliverable: the visual shell — a dark, enterprise-style window with a
room sidebar and a main panel that renders the selected room's devices as a
grid of cards grouped by category, driven by the typed models from
``models.py``.

What is intentionally *not* here yet (Step 4):
  * the live async/threaded status check behind the "Check Status" button, and
  * automatic re-checking.

The seams for that are already in place: :meth:`App.set_device_status` updates a
single card's indicator live, and :meth:`App.set_room_status` bulk-updates the
current room, so the Step 4 ping worker only has to call these from the UI
thread.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from mission_deck import __app_name__, __version__
from mission_deck.browser import BrowserConfig, open_urls
from mission_deck.network import CheckResult, run_status_checks
from mission_deck.config import (
    ConfigError,
    ConfigNotFoundError,
    find_config,
    load_config,
)
from mission_deck.models import Device, DeviceStatus, Room, Site
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

        # Subtle hover affordance (cards become controllable later).
        for widget in (self, self._dot, self._name, self._subtitle, self._meta):
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

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
# Main application window
# --------------------------------------------------------------------------- #
class App(ctk.CTk):
    def __init__(self, site: Site, config_path: Path | None = None):
        super().__init__()
        self.site = site
        self.config_path = config_path
        self.browser_cfg = BrowserConfig.from_settings(site.settings)
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

        # --- Window chrome -------------------------------------------------- #
        ctk.set_appearance_mode("dark")
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
            self._card_pool.append(DeviceCard(self._grid))
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

        timeout = self.site.ping_timeout_seconds
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


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def _prompt_for_config() -> Path | None:
    """Ask the user to pick a config.json via a native file dialog."""

    root = ctk.CTk()
    root.withdraw()
    chosen = filedialog.askopenfilename(
        title="Select a mission-deck config.json",
        filetypes=[("JSON config", "*.json"), ("All files", "*.*")],
    )
    root.destroy()
    return Path(chosen) if chosen else None


def load_site_interactive() -> tuple[Site, Path | None]:
    """Locate + load a config, prompting the user if none is found.

    Falls back to the bundled example data only if the user cancels the picker,
    so the app always has something to render.
    """

    path = find_config()
    if path is None:
        chosen = _prompt_for_config()
        if chosen is not None:
            path = chosen

    if path is None:
        # Last resort: example data, clearly labelled in the footer.
        example = Path(__file__).resolve().parent.parent / "config.example.json"
        return Site.from_loaded_config(load_config(example)), None

    return Site.from_loaded_config(load_config(path)), path


def main() -> None:
    try:
        site, path = load_site_interactive()
    except ConfigNotFoundError:
        messagebox.showerror(__app_name__, "No configuration file was selected.")
        return
    except ConfigError as exc:
        messagebox.showerror(__app_name__, f"Could not load configuration:\n\n{exc}")
        return

    app = App(site, config_path=path)
    app.mainloop()


if __name__ == "__main__":
    main()
