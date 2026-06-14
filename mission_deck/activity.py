"""The Activity Log view — a browsable window onto the audit trail.

mission-deck already records every operator action as a JSON line in
``audit.log`` (see :mod:`mission_deck.logging_setup`): who issued which command,
to which device, when, and whether it succeeded. The Overview shows the last
handful; this view is the full-screen, filterable reader an AV team reaches for
when asked "who touched that recorder, and when?".

This screen only appears once the **Activity Log** plugin is activated on the
Plugins page. Pure presentation, mirroring :mod:`mission_deck.cloud`: it reads
:func:`logging_setup.tail_audit` (best-effort, read-only) and never writes;
nothing here touches the network or a worker thread.
"""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk

from mission_deck.icons import icon
from mission_deck.logging_setup import audit_log_path, tail_audit
from mission_deck.theme import COLORS, CORNER, CORNER_LG, GAP, PAD
from mission_deck.ui import BTN_OUTLINE, font

# How many recent events to pull from the audit log per refresh — plenty for a
# compliance scan without dragging a rotated multi-MB log into memory.
_MAX_EVENTS = 250

# Fields, in priority order, that make up a one-line summary of an event.
_DETAIL_KEYS = ("device_name", "room_name", "control", "command", "host", "url", "plugin")


def _event_color(event: str) -> str:
    """Tint an event by family so the eye separates the kinds of action.

    Azure for operator/control actions, amber for config/settings changes, green
    for exports, muted for routine sweeps, faint grey for lifecycle noise.
    """

    if event.startswith(("device.", "room.", "cloud.")):
        return COLORS["accent2_text"]
    if event.startswith(("config.", "settings.")):
        return COLORS["warn"]
    if event.startswith("report."):
        return COLORS["online"]
    if event.startswith("status_check"):
        return COLORS["text_muted"]
    return COLORS["text_faint"]


def _detail(event: dict) -> str:
    """Assemble a readable one-liner from an event's known fields."""

    bits = [str(event[key]) for key in _DETAIL_KEYS if event.get(key)]
    if "ok" in event:
        bits.append("ok" if event.get("ok") else "FAILED")
    if event.get("error"):
        bits.append(str(event["error"]))
    return "   ·   ".join(bits)


def _fmt_ts(raw) -> str:
    """Format an ISO-8601 (UTC) audit timestamp in the operator's local zone."""

    try:
        return datetime.fromisoformat(str(raw)).astimezone().strftime("%Y-%m-%d  %H:%M:%S")
    except (ValueError, TypeError):
        return str(raw or "")


def _searchable(event: dict) -> str:
    """Lower-cased haystack for the filter box (event name + user + detail)."""

    return " ".join((
        str(event.get("event", "")),
        str(event.get("user", "")),
        _detail(event),
    )).lower()


class _Row:
    """A pooled activity row: its widgets are built once and rebound per event.

    Rebuilding a CTk widget tree is expensive, so the view keeps a pool of these
    and reconfigures them in place (matching the ``DeviceCard``/``RoomButton``
    pooling elsewhere) instead of destroying and recreating hundreds of widgets
    on every refresh or keystroke.
    """

    def __init__(self, master) -> None:
        self.frame = ctk.CTkFrame(master, corner_radius=CORNER, fg_color=COLORS["card"])
        self.frame.grid_columnconfigure(1, weight=1)
        # Left accent rule encodes the action family.
        self._accent = ctk.CTkFrame(self.frame, width=3, corner_radius=2)
        self._accent.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(0, GAP), pady=8)
        self._name = ctk.CTkLabel(
            self.frame, anchor="w", font=font(12, mono=True, weight="bold"),
        )
        self._name.grid(row=0, column=1, sticky="ew", pady=(8, 0))
        self._detail = ctk.CTkLabel(
            self.frame, anchor="w", justify="left", wraplength=640,
            font=font(11), text_color=COLORS["text_muted"],
        )
        self._detail.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        self._meta = ctk.CTkLabel(
            self.frame, anchor="e", justify="right",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._meta.grid(row=0, column=2, rowspan=2, sticky="e", padx=(GAP, 12))

    def bind(self, row: int, event: dict) -> None:
        name = str(event.get("event", "event"))
        accent = _event_color(name)
        self._accent.configure(fg_color=accent)
        self._name.configure(text=name, text_color=accent)
        self._detail.configure(text=_detail(event) or "—")
        meta = _fmt_ts(event.get("ts"))
        user = str(event.get("user", "")).strip()
        self._meta.configure(text=f"{meta}\n{user}" if user else meta)
        self.frame.grid(row=row, column=0, sticky="ew", pady=2)

    def hide(self) -> None:
        self.frame.grid_remove()


class ActivityView(ctk.CTkScrollableFrame):
    """Full-screen, filterable reader for the local audit trail."""

    def __init__(self, master, app: "App"):  # noqa: F821 - App imported lazily
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._filter = ctk.StringVar()
        # Events are loaded from disk only on an explicit refresh and cached here;
        # filtering works off this list so a keystroke never re-reads the log.
        self._events: list[dict] = []
        self._rows: list[_Row] = []
        self._empty_label: ctk.CTkLabel | None = None
        self._filter_job: str | None = None
        self._sig: tuple | None = None  # last painted (needle, events) signature
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._list = ctk.CTkFrame(self, fg_color="transparent")
        self._list.grid(row=1, column=0, sticky="nsew", pady=(GAP, 0))
        self._list.grid_columnconfigure(0, weight=1)
        self._filter.trace_add("write", lambda *_: self._on_filter_changed())
        self.refresh()

    # ------------------------------------------------------------------ #
    def _build_header(self) -> None:
        hero = ctk.CTkFrame(
            self, corner_radius=CORNER_LG, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        hero.grid(row=0, column=0, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(hero, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            head, text="", image=icon("clock", 22, COLORS["accent2_text"]),
        ).grid(row=0, column=0, rowspan=2, padx=(0, GAP), sticky="n")
        ctk.CTkLabel(
            head, text="Activity log", anchor="w",
            font=font(18, weight="bold"), text_color=COLORS["text"],
        ).grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(
            head, text="Every operator action mission-deck records, newest first. "
            "Filter by device, room or action; refresh as new actions land.",
            anchor="w", justify="left", wraplength=720,
            font=font(12), text_color=COLORS["text_muted"],
        ).grid(row=1, column=1, sticky="ew", pady=(2, 0))

        # Toolbar: filter entry + count + refresh.
        bar = ctk.CTkFrame(hero, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=PAD, pady=(6, PAD))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(
            bar, textvariable=self._filter, height=34, corner_radius=CORNER,
            placeholder_text="Filter events — device, room, action, user…",
            border_width=1, border_color=COLORS["border_2"], fg_color=COLORS["card_2"],
            font=font(12), text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="ew")
        self._count = ctk.CTkLabel(
            bar, text="", width=120, anchor="e",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._count.grid(row=0, column=1, padx=(GAP, GAP))
        ctk.CTkButton(
            bar, text="REFRESH", width=110, height=34,
            font=font(11, mono=True), command=self.refresh, **BTN_OUTLINE,
        ).grid(row=0, column=2)

    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Reload events from disk, then repaint. The REFRESH button / first show."""

        self._events = tail_audit(_MAX_EVENTS)
        self._apply_filter()

    def _on_filter_changed(self) -> None:
        """Debounce keystrokes: re-filter the cached events after a short pause."""

        if self._filter_job is not None:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(200, self._apply_filter)

    def _apply_filter(self) -> None:
        """Filter the cached events and repaint — no disk read, no widget churn."""

        self._filter_job = None
        needle = self._filter.get().strip().lower()
        if needle:
            events = [e for e in self._events if needle in _searchable(e)]
        else:
            events = self._events

        total = len(events)
        self._count.configure(text=f"{total} event{'s' if total != 1 else ''}")

        # Skip the repaint entirely when the filtered set hasn't changed (e.g. a
        # REFRESH that found nothing new), mirroring the dashboard's signature guard.
        signature = (needle, tuple(
            (e.get("ts"), e.get("event"), e.get("user")) for e in events
        ))
        if signature == self._sig:
            return
        self._sig = signature

        if not events:
            self._show_empty()
            return
        self._hide_empty()
        for index, event in enumerate(events):
            if index < len(self._rows):
                row = self._rows[index]
            else:
                row = _Row(self._list)
                self._rows.append(row)
            row.bind(index, event)
        for surplus in self._rows[len(events):]:
            surplus.hide()

    def _show_empty(self) -> None:
        for row in self._rows:
            row.hide()
        path = audit_log_path()
        if path is None:
            msg = "Audit logging is disabled (no writable log directory)."
        elif self._filter.get().strip():
            msg = "No events match that filter."
        else:
            msg = "No activity recorded yet — operator actions will appear here."
        if self._empty_label is None:
            self._empty_label = ctk.CTkLabel(
                self._list, anchor="w",
                font=font(12), text_color=COLORS["text_faint"],
            )
        self._empty_label.configure(text=msg)
        self._empty_label.grid(row=0, column=0, sticky="w", padx=4, pady=PAD)

    def _hide_empty(self) -> None:
        if self._empty_label is not None:
            self._empty_label.grid_remove()
