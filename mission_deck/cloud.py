"""The Cloud Sync view — the OneDrive config-source concept.

mission-deck's config discovery order (env var → CWD → app dir → per-user dir)
will gain a cloud-backed option: a config source that delivers ``config.json``
to every workstation, with revisioned pull/push and a conflict-aware merge.
Credentials are per-user; no estate data lives in the repo.

This screen only appears once the **Cloud Sync** plugin is activated on the
Plugins page. It is deliberately a "coming soon" preview — the connect / sync
affordances are not wired to a real Graph API client yet. It shows the shape of
the feature (the estate it *would* sync is read live from the loaded site) plus
the value props, leaving the base in place for when the integration is built.
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
from mission_deck.ui import font

_FEATURES = [
    ("Multi-workstation", "Every operator PC loads one authoritative config — no "
     "more emailing JSON or hand-editing per machine."),
    ("Revisioned", "Each push is a OneDrive version. Roll back a bad edit, diff "
     "what changed, see who changed it."),
    ("Offline-safe", "The last-good config is cached locally. If OneDrive is "
     "unreachable, mission-deck keeps running on the cached copy."),
]


class CloudView(ctk.CTkScrollableFrame):
    """OneDrive config-source screen (presentation-only placeholder)."""

    def __init__(self, master, app: "App"):  # noqa: F821 - App imported lazily
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)

        self._build_hero()

        note = spec_note(
            self,
            "Cloud Sync is a config source plugin. The discovery order gains a "
            "cloud-backed option: a Graph API client delivers config.json to "
            "every workstation, with revisioned pull/push and a three-way merge "
            "on conflict. Credentials are per-user; no estate data lives in the "
            "repo.  (Coming soon.)",
        )
        note.grid(row=1, column=0, sticky="ew", pady=(GAP, 0))

        self._build_feature_cards()

    # ------------------------------------------------------------------ #
    def _build_hero(self) -> None:
        hero = ctk.CTkFrame(
            self, corner_radius=CORNER_LG, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        hero.grid(row=0, column=0, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)

        # Centred placeholder block.
        inner = ctk.CTkFrame(hero, fg_color="transparent")
        inner.grid(row=0, column=0, pady=(46, 12))
        ctk.CTkLabel(
            inner, text="", image=icon("cloud", 52, COLORS["ghost"]),
        ).pack()
        ctk.CTkLabel(
            inner, text="Cloud Sync is coming soon", anchor="center",
            font=font(18, weight="bold"), text_color=COLORS["text"],
        ).pack(pady=(10, 2))
        ctk.CTkLabel(
            inner, text="Sync config.json from OneDrive / SharePoint so every "
            "workstation loads the same estate.",
            anchor="center", justify="center", wraplength=520,
            font=font(12), text_color=COLORS["text_muted"],
        ).pack()

        # The estate it would sync — real numbers from the loaded site.
        device_count = len(list(self.app.site.all_devices()))
        room_count = len(self.app.site.rooms)
        chip = ctk.CTkFrame(
            hero, corner_radius=CORNER, fg_color=COLORS["card_2"],
            border_width=1, border_color=COLORS["border"],
        )
        chip.grid(row=1, column=0, pady=(0, 8))
        ctk.CTkLabel(
            chip, text=f"  {room_count} rooms · {device_count} devices ready to sync  ",
            image=icon("folder", 14, COLORS["text_muted"]), compound="left",
            height=30, font=font(12, mono=True),
            text_color=COLORS["text_muted"],
        ).pack(padx=PAD)

        # Base CTA — disabled until the Graph client lands.
        ctk.CTkButton(
            hero, text="Connect OneDrive", image=icon("cloud", 15, COLORS["text_faint"]),
            compound="left", width=180, height=34, corner_radius=CORNER,
            fg_color=COLORS["card_2"], hover_color=COLORS["card_2"],
            border_width=1, border_color=COLORS["border"],
            text_color=COLORS["text_faint"], font=font(13, weight="bold"),
            state="disabled", command=lambda: None,
        ).grid(row=2, column=0, pady=(0, 46))

    # ------------------------------------------------------------------ #
    def _build_feature_cards(self) -> None:
        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.grid(row=2, column=0, sticky="ew", pady=(GAP, 0))
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
                font=font(13, weight="bold"), text_color=COLORS["text"],
            ).pack(anchor="w", padx=PAD, pady=(PAD, 2))
            ctk.CTkLabel(
                card, text=desc, anchor="w", justify="left", wraplength=300,
                font=font(12), text_color=COLORS["text_muted"],
            ).pack(anchor="w", padx=PAD, pady=(0, PAD))
