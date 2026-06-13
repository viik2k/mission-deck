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


class ActivityView(ctk.CTkScrollableFrame):
    """Full-screen, filterable reader for the local audit trail."""

    def __init__(self, master, app: "App"):  # noqa: F821 - App imported lazily
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._filter = ctk.StringVar()
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._list = ctk.CTkFrame(self, fg_color="transparent")
        self._list.grid(row=1, column=0, sticky="nsew", pady=(GAP, 0))
        self._list.grid_columnconfigure(0, weight=1)
        self._filter.trace_add("write", lambda *_: self.refresh())
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
        for child in self._list.winfo_children():
            child.destroy()

        events = tail_audit(_MAX_EVENTS)
        needle = self._filter.get().strip().lower()
        if needle:
            events = [e for e in events if needle in _searchable(e)]

        total = len(events)
        self._count.configure(text=f"{total} event{'s' if total != 1 else ''}")

        if not events:
            self._empty()
            return
        for row, event in enumerate(events):
            self._row(row, event)

    def _empty(self) -> None:
        path = audit_log_path()
        if path is None:
            msg = "Audit logging is disabled (no writable log directory)."
        elif self._filter.get().strip():
            msg = "No events match that filter."
        else:
            msg = "No activity recorded yet — operator actions will appear here."
        ctk.CTkLabel(
            self._list, text=msg, anchor="w",
            font=font(12), text_color=COLORS["text_faint"],
        ).grid(row=0, column=0, sticky="w", padx=4, pady=PAD)

    def _row(self, row: int, event: dict) -> None:
        name = str(event.get("event", "event"))
        accent = _event_color(name)
        line = ctk.CTkFrame(self._list, corner_radius=CORNER, fg_color=COLORS["card"])
        line.grid(row=row, column=0, sticky="ew", pady=2)
        line.grid_columnconfigure(1, weight=1)

        # Left accent rule encodes the action family.
        ctk.CTkFrame(line, width=3, corner_radius=2, fg_color=accent).grid(
            row=0, column=0, rowspan=2, sticky="ns", padx=(0, GAP), pady=8)

        ctk.CTkLabel(
            line, text=name, anchor="w",
            font=font(12, mono=True, weight="bold"), text_color=accent,
        ).grid(row=0, column=1, sticky="ew", pady=(8, 0))
        detail = _detail(event)
        ctk.CTkLabel(
            line, text=detail or "—", anchor="w", justify="left", wraplength=640,
            font=font(11), text_color=COLORS["text_muted"],
        ).grid(row=1, column=1, sticky="ew", pady=(0, 8))

        meta = _fmt_ts(event.get("ts"))
        user = str(event.get("user", "")).strip()
        ctk.CTkLabel(
            line, text=f"{meta}\n{user}" if user else meta, anchor="e", justify="right",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        ).grid(row=0, column=2, rowspan=2, sticky="e", padx=(GAP, 12))
