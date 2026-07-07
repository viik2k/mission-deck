"""Global command palette for mission-deck (Ctrl+K).

A keyboard-first jump box layered over the main window: type a few letters,
press Enter, and you are in the room / device / action you meant — no clicking
through cities or scanning card grids. It searches three kinds of targets:

* **Actions** — estate sweep, room check, open web UIs, export, settings…
* **Rooms** — by name, city, location or id; selecting one jumps to its view.
* **Devices** — by name, host, type or room; selecting one jumps to its room
  and opens its control dialog.

It is pure presentation: items are built from the live :class:`Site` when the
palette opens, every action is a callback into :class:`App`, and it never
touches the network or config itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import customtkinter as ctk

from mission_deck.icons import category_icon_name, icon
from mission_deck.theme import COLORS, CORNER, GAP, status_color
from mission_deck.ui import font

if TYPE_CHECKING:  # imported lazily by app.py — avoid a circular import
    from mission_deck.app import App

# How many matches are shown at once (the list is ranked, worst dropped).
MAX_RESULTS = 12
_WIDTH = 620


@dataclass(slots=True)
class PaletteItem:
    """One runnable search result: what to show, what to match, what to do."""

    label: str
    detail: str
    icon_name: str
    icon_color: str
    haystack: str               # lowercase text the query tokens match against
    action: Callable[[], None]


class _Row(ctk.CTkFrame):
    """A pooled result row: icon + label + right-aligned detail."""

    def __init__(self, master, on_click: Callable[["_Row"], None]):
        super().__init__(master, corner_radius=CORNER, fg_color="transparent", height=36)
        self.index = 0
        self.grid_columnconfigure(1, weight=1)
        self._icon = ctk.CTkLabel(self, text="", width=22)
        self._icon.grid(row=0, column=0, padx=(10, 8), pady=7)
        self._label = ctk.CTkLabel(
            self, text="", anchor="w",
            font=font(12), text_color=COLORS["text"],
        )
        self._label.grid(row=0, column=1, sticky="ew")
        self._detail = ctk.CTkLabel(
            self, text="", anchor="e",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._detail.grid(row=0, column=2, sticky="e", padx=(GAP, 12))
        for widget in (self, self._icon, self._label, self._detail):
            widget.bind("<Button-1>", lambda _e: on_click(self))
            widget.configure(cursor="hand2")

    def show(self, item: PaletteItem, selected: bool) -> None:
        self._icon.configure(image=icon(item.icon_name, 15, item.icon_color))
        self._label.configure(text=item.label)
        self._detail.configure(text=item.detail)
        self.set_selected(selected)
        if not self.winfo_manager():
            self.pack(fill="x", padx=6, pady=1)

    def set_selected(self, selected: bool) -> None:
        self.configure(fg_color=COLORS["border_strong"] if selected else "transparent")
        self._label.configure(text_color="#FFFFFF" if selected else COLORS["text"])

    def hide(self) -> None:
        if self.winfo_manager():
            self.pack_forget()


class CommandPalette(ctk.CTkToplevel):
    """The Ctrl+K overlay. Created on demand, destroyed on close/run/Escape."""

    def __init__(self, app: "App"):
        super().__init__(app)
        self.app = app
        self._items = self._build_items()
        self._visible: list[PaletteItem] = []
        self._selected = 0

        # Borderless overlay near the top of the main window; the outer frame
        # colour shows through 1px of padding as a hairline border.
        self.overrideredirect(True)
        self.configure(fg_color=COLORS["border_2"])
        x = app.winfo_rootx() + max(0, (app.winfo_width() - _WIDTH) // 2)
        y = app.winfo_rooty() + 84
        self.geometry(f"+{x}+{y}")

        body = ctk.CTkFrame(self, corner_radius=CORNER, fg_color=COLORS["elev"])
        body.pack(fill="both", expand=True, padx=1, pady=1)

        self._query = ctk.StringVar()
        self._query.trace_add("write", lambda *_: self._refilter())
        self._entry = ctk.CTkEntry(
            body, textvariable=self._query, width=_WIDTH - 14, height=40,
            corner_radius=CORNER, border_width=0, fg_color=COLORS["elev"],
            placeholder_text="Jump to a room, device or action…",
            font=font(13), text_color=COLORS["text"],
        )
        self._entry.pack(fill="x", padx=6, pady=(6, 2))
        ctk.CTkFrame(body, height=1, corner_radius=0, fg_color=COLORS["border"]).pack(fill="x")

        self._results = ctk.CTkFrame(body, fg_color="transparent")
        self._results.pack(fill="both", expand=True, pady=(4, 0))
        self._rows = [_Row(self._results, self._on_row_click) for _ in range(MAX_RESULTS)]
        self._empty = ctk.CTkLabel(
            self._results, text="No matches.",
            font=font(12), text_color=COLORS["text_faint"],
        )

        ctk.CTkLabel(
            body, text="↑↓ NAVIGATE   ·   ENTER RUN   ·   ESC CLOSE",
            font=font(9, mono=True), text_color=COLORS["text_faint"],
        ).pack(pady=(4, 8))

        self.bind("<Down>", self._move(1))
        self.bind("<Up>", self._move(-1))
        self.bind("<Return>", lambda _e: self._run_selected())
        self.bind("<Escape>", lambda _e: self.close())
        # With the grab held, a click outside the palette is delivered to us
        # with out-of-bounds coordinates — treat it as a dismiss.
        self.bind("<Button-1>", self._maybe_close_outside)

        self._refilter()
        self.after(60, self._focus)

    # ------------------------------------------------------------------ #
    # Items
    # ------------------------------------------------------------------ #
    def _build_items(self) -> list[PaletteItem]:
        app = self.app
        items: list[PaletteItem] = []
        muted = COLORS["text_muted"]

        def add(label: str, detail: str, icon_name: str, color: str,
                action: Callable[[], None], keywords: str = "") -> None:
            items.append(PaletteItem(
                label, detail, icon_name, color,
                f"{label} {detail} {keywords}".lower(), action,
            ))

        # Actions first so bare verbs ("sweep", "export") beat device names.
        add("Run estate sweep", "check every device", "refresh", muted,
            app.run_estate_sweep, "refresh all status estate")
        room = app.current_room
        if room is not None:
            add(f"Check status — {room.name}", "current room", "check", muted,
                app.on_check_status, "status check ping probe")
            add(f"Open web UIs — {room.name}", "current room", "external", muted,
                app.on_open_web_uis, "browser launch web")
        add("Export status report", "CSV", "doc", muted,
            app.on_export_report, "csv report save export")
        add("Open settings", "preferences", "settings", muted,
            app.open_settings, "settings preferences timeout browser appearance")
        add("Keyboard shortcuts", "F1", "command", muted,
            app.open_shortcuts, "help keys hotkeys reference")
        add("Switch configuration", "open another config file", "folder", muted,
            app.switch_config_dialog, "config json file open switch")
        add("Add dashboard widget", "widget catalogue", "dashboards", muted,
            app.open_widget_picker, "dashboard board kpi trend compose")
        add("Go to Overview", "view", "overview", muted,
            lambda: app.navigate("overview"), "dashboard estate home")
        add("Go to Rooms", "view", "rooms", muted, lambda: app.navigate("rooms"))
        add("Go to Dashboards", "view", "dashboards", muted,
            lambda: app.navigate("dashboards"), "board widgets")
        add("Go to Plugins", "view", "plugins", muted, lambda: app.navigate("plugins"))

        for r in app.site.rooms:
            place = " · ".join(p for p in (r.city, r.location) if p)
            add(r.name, place or "room", "rooms", muted,
                lambda rm=r: app.select_room(rm), f"room {r.id}")

        for r in app.site.rooms:
            for device in r.devices:
                add(device.name, f"{r.name} · {device.address}",
                    category_icon_name(device.category), status_color(device.status),
                    lambda rm=r, d=device: app.open_device(rm, d),
                    f"device {device.type} {device.category} {device.host} {device.id}")
                if device.stream_url:
                    add(f"Live view — {device.name}", r.name, "video", muted,
                        lambda rm=r, d=device: app.open_live_view(rm, d),
                        f"live view stream video feed camera rtsp rtmp {device.id}")
        return items

    def _filter(self, query: str) -> list[PaletteItem]:
        q = query.strip().lower()
        if not q:
            return self._items[:MAX_RESULTS]
        tokens = q.split()
        matches = [it for it in self._items if all(t in it.haystack for t in tokens)]
        # Stable sort: label-prefix matches bubble up, original (actions →
        # rooms → devices) order is preserved within each band.
        matches.sort(key=lambda it: 0 if it.label.lower().startswith(tokens[0]) else 1)
        return matches[:MAX_RESULTS]

    # ------------------------------------------------------------------ #
    # Rendering / selection
    # ------------------------------------------------------------------ #
    def _refilter(self) -> None:
        self._visible = self._filter(self._query.get())
        self._selected = 0
        for index, row in enumerate(self._rows):
            if index < len(self._visible):
                row.index = index
                row.show(self._visible[index], selected=index == self._selected)
            else:
                row.hide()
        if self._visible:
            self._empty.pack_forget()
        elif not self._empty.winfo_manager():
            self._empty.pack(pady=10)

    def _move(self, delta: int):
        def handler(_event=None):
            if not self._visible:
                return "break"
            self._rows[self._selected].set_selected(False)
            self._selected = (self._selected + delta) % len(self._visible)
            self._rows[self._selected].set_selected(True)
            return "break"
        return handler

    def _on_row_click(self, row: _Row) -> None:
        self._selected = row.index
        self._run_selected()

    def _run_selected(self) -> None:
        if not self._visible:
            return
        item = self._visible[self._selected]
        app = self.app
        self.close()
        # Run after the grab is released so dialogs the action opens can take it.
        app.after(10, item.action)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def _focus(self) -> None:
        # Focus/grab can fail transiently on some window managers; cosmetic.
        try:
            self.lift()
            self.grab_set()
            self._entry.focus_set()
        except Exception:
            pass

    def _maybe_close_outside(self, event) -> None:
        if not (0 <= event.x_root - self.winfo_rootx() <= self.winfo_width()
                and 0 <= event.y_root - self.winfo_rooty() <= self.winfo_height()):
            self.close()

    def close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
