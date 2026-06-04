"""The Cloud Sync view — the OneDrive config-source concept.

mission-deck's config discovery order (env var → CWD → app dir → per-user dir)
gains a cloud-backed option: a config source that delivers ``config.json`` to
every workstation, with revisioned pull/push and a conflict-aware merge.
Credentials are per-user; no estate data lives in the repo.

This is a presentation-only placeholder ("coming soon"): the *Sync now*,
*Push*, and *Configure* affordances are not yet wired to a real Graph API
client. The live device count is read from the loaded site so the panel
reflects the real estate size.
"""

from __future__ import annotations

import customtkinter as ctk

from mission_deck.icons import icon
from mission_deck.plugins import spec_note
from mission_deck.theme import (
    COLORS,
    CORNER,
    CORNER_LG,
    GAP,
    PAD,
)

# Illustrative sync timeline. (mark: ok / push / conflict)
SYNC_LOG: list[tuple[str, str, str, str]] = [
    ("ok", "Pulled config.json", "rev 184 · scheduled sync · no conflicts", "14:48"),
    ("push", "Pushed local edit", "Courtroom 3C · added Poly Studio X50", "13:59"),
    ("conflict", "Merge conflict resolved", "kept remote · Courtroom 7 host change", "11:28"),
    ("ok", "Pulled config.json", "rev 182 · scheduled sync", "09:00"),
    ("ok", "Connected OneDrive", "AV-Estate / mission-deck / config.json", "Mon 08:14"),
]

_FEATURES = [
    ("Multi-workstation", "Every operator PC loads one authoritative config — no "
     "more emailing JSON or hand-editing per machine."),
    ("Revisioned", "Each push is a OneDrive version. Roll back a bad edit, diff "
     "what changed, see who changed it."),
    ("Offline-safe", "The last-good config is cached locally. If OneDrive is "
     "unreachable, mission-deck keeps running on the cached copy."),
]


def _panel(master) -> ctk.CTkFrame:
    return ctk.CTkFrame(
        master, corner_radius=CORNER_LG, fg_color=COLORS["card"],
        border_width=1, border_color=COLORS["border"],
    )


def _panel_header(panel, title: str, sub: str = "", icon_name: str | None = None) -> None:
    head = ctk.CTkFrame(panel, fg_color="transparent")
    head.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD - 2, 0))
    if icon_name:
        ctk.CTkLabel(
            head, text="", image=icon(icon_name, 16, COLORS["info"]),
        ).pack(side="left", padx=(0, 8))
    ctk.CTkLabel(
        head, text=title, anchor="w",
        font=ctk.CTkFont(size=13, weight="bold"), text_color=COLORS["text"],
    ).pack(side="left")
    if sub:
        ctk.CTkLabel(
            head, text=sub, anchor="e",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_faint"],
        ).pack(side="right")


class CloudView(ctk.CTkScrollableFrame):
    """OneDrive config-source screen (presentation-only placeholder)."""

    def __init__(self, master, app: "App"):  # noqa: F821 - App imported lazily
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=2, uniform="cloud")
        self.grid_columnconfigure(1, weight=1, uniform="cloud")

        self._build_source_panel()
        self._build_activity_panel()

        note = spec_note(
            self,
            "Cloud Sync is a config source plugin. The discovery order gains a "
            "cloud-backed option: a Graph API client delivers config.json to "
            "every workstation, with revisioned pull/push and a three-way merge "
            "on conflict. Credentials are per-user; no estate data lives in the "
            "repo.  (Coming soon.)",
        )
        note.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(GAP, 0))

        self._build_feature_cards()

    # ------------------------------------------------------------------ #
    def _build_source_panel(self) -> None:
        panel = _panel(self)
        panel.grid(row=0, column=0, sticky="nsew", padx=(0, GAP // 2))
        panel.grid_columnconfigure(0, weight=1)
        _panel_header(panel, "OneDrive — connected", "AV-Estate · tenant", icon_name="cloud")

        body = ctk.CTkFrame(panel, fg_color="transparent")
        body.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)
        body.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkLabel(
            body, text="CONFIG SOURCE", anchor="w",
            font=ctk.CTkFont(size=10, weight="bold"), text_color=COLORS["text_faint"],
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        ctk.CTkLabel(
            body, text="  AV-Estate / mission-deck / config.json", anchor="w",
            image=icon("folder", 14, COLORS["text_muted"]), compound="left",
            height=34, corner_radius=CORNER, fg_color=COLORS["card_2"],
            font=ctk.CTkFont(size=12, family="Consolas"), text_color=COLORS["text"],
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, GAP))

        device_count = len(list(self.app.site.all_devices()))
        room_count = len(self.app.site.rooms)
        stats = [
            ("REVISION", "rev 184", COLORS["text"]),
            ("LAST PULLED", "4 min ago", COLORS["text"]),
            ("ROOMS · DEVICES", f"{room_count} · {device_count}", COLORS["text"]),
            ("AUTO-SYNC", "every 15 min", COLORS["online"]),
        ]
        for index, (label, value, color) in enumerate(stats):
            row, col = divmod(index, 2)
            cell = ctk.CTkFrame(
                body, corner_radius=CORNER, fg_color=COLORS["card_2"],
                border_width=1, border_color=COLORS["border"],
            )
            cell.grid(
                row=2 + row, column=col, sticky="ew",
                padx=(0 if col == 0 else GAP // 2, 0 if col == 1 else GAP // 2),
                pady=(0, GAP),
            )
            ctk.CTkLabel(
                cell, text=label, anchor="w",
                font=ctk.CTkFont(size=10), text_color=COLORS["text_faint"],
            ).pack(anchor="w", padx=GAP, pady=(8, 0))
            ctk.CTkLabel(
                cell, text=value, anchor="w",
                font=ctk.CTkFont(size=14, family="Consolas"), text_color=color,
            ).pack(anchor="w", padx=GAP, pady=(0, 8))

        # Conflict banner.
        conflict = ctk.CTkFrame(
            body, corner_radius=CORNER, fg_color="#2a2212",
            border_width=1, border_color=COLORS["warn"],
        )
        conflict.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, GAP))
        conflict.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            conflict, text="", image=icon("warn", 20, COLORS["warn"]),
        ).grid(row=0, column=0, rowspan=2, padx=(GAP, 6), pady=GAP)
        ctk.CTkLabel(
            conflict, text="1 local edit not yet pushed", anchor="w",
            font=ctk.CTkFont(size=12, weight="bold"), text_color=COLORS["text"],
        ).grid(row=0, column=1, sticky="w", pady=(GAP, 0))
        ctk.CTkLabel(
            conflict, text="Courtroom 3C · added Poly Studio X50 backup VC", anchor="w",
            font=ctk.CTkFont(size=11), text_color=COLORS["text_muted"],
        ).grid(row=1, column=1, sticky="w", pady=(0, GAP))
        ctk.CTkButton(
            conflict, text="Push", image=icon("refresh", 14, "#ffffff"), compound="left",
            width=80, height=30, corner_radius=CORNER,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            font=ctk.CTkFont(size=12, weight="bold"), command=lambda: None,
        ).grid(row=0, column=2, rowspan=2, padx=(0, GAP))

        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.grid(row=5, column=0, columnspan=2, sticky="w")
        for label, name in (("Revision history", "history"), ("Preview remote", "eye"),
                            ("Change file…", "folder")):
            ctk.CTkButton(
                actions, text=label, image=icon(name, 14, COLORS["text_muted"]), compound="left",
                height=30, corner_radius=CORNER,
                fg_color="transparent", hover_color=COLORS["card_hover"],
                border_width=1, border_color=COLORS["border"], text_color=COLORS["text_muted"],
                font=ctk.CTkFont(size=12), command=lambda: None,
            ).pack(side="left", padx=(0, GAP))

    # ------------------------------------------------------------------ #
    def _build_activity_panel(self) -> None:
        panel = _panel(self)
        panel.grid(row=0, column=1, sticky="nsew", padx=(GAP // 2, 0))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        _panel_header(panel, "Sync activity", icon_name="history")

        body = ctk.CTkFrame(panel, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=PAD, pady=PAD)
        body.grid_columnconfigure(1, weight=1)
        mark_color = {
            "ok": COLORS["online"], "push": COLORS["accent"], "conflict": COLORS["warn"],
        }
        for index, (mark, title, detail, when) in enumerate(SYNC_LOG):
            ctk.CTkLabel(
                body, text="●", width=14, font=ctk.CTkFont(size=12),
                text_color=mark_color.get(mark, COLORS["accent"]),
            ).grid(row=index, column=0, sticky="n", pady=(6, 0))
            text = ctk.CTkFrame(body, fg_color="transparent")
            text.grid(row=index, column=1, sticky="ew", pady=(4, 4))
            text.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                text, text=title, anchor="w",
                font=ctk.CTkFont(size=12), text_color=COLORS["text"],
            ).grid(row=0, column=0, sticky="ew")
            ctk.CTkLabel(
                text, text=detail, anchor="w", justify="left", wraplength=220,
                font=ctk.CTkFont(size=11, family="Consolas"), text_color=COLORS["text_faint"],
            ).grid(row=1, column=0, sticky="ew")
            ctk.CTkLabel(
                body, text=when, anchor="e",
                font=ctk.CTkFont(size=11, family="Consolas"), text_color=COLORS["text_faint"],
            ).grid(row=index, column=2, sticky="ne", padx=(GAP, 0), pady=(4, 0))

    # ------------------------------------------------------------------ #
    def _build_feature_cards(self) -> None:
        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(GAP, 0))
        for col in range(3):
            cards.grid_columnconfigure(col, weight=1, uniform="feat")
        for index, (title, desc) in enumerate(_FEATURES):
            card = ctk.CTkFrame(
                cards, corner_radius=CORNER_LG, fg_color=COLORS["card"],
                border_width=1, border_color=COLORS["border"],
            )
            card.grid(
                row=0, column=index, sticky="nsew",
                padx=(0 if index == 0 else GAP // 2, 0 if index == 2 else GAP // 2),
            )
            ctk.CTkLabel(
                card, text=title, anchor="w",
                font=ctk.CTkFont(size=13, weight="bold"), text_color=COLORS["text"],
            ).pack(anchor="w", padx=PAD, pady=(PAD, 2))
            ctk.CTkLabel(
                card, text=desc, anchor="w", justify="left", wraplength=300,
                font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
            ).pack(anchor="w", padx=PAD, pady=(0, PAD))
