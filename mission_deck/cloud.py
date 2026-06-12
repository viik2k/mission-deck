"""The Cloud Sync view — a working cloud config source.

mission-deck's config can be distributed centrally: point every operator
workstation at one HTTPS URL (a OneDrive/SharePoint direct-download link, an
intranet web server, a Git raw URL — anything that serves the JSON) and each
sync pulls, validates and caches it locally as ``cloud-config.json`` in the
per-user directory. The app then loads the *cached* copy, so an unreachable
source never strands a courtroom — the last-good config keeps working.

This screen only appears once the **Cloud Sync** plugin is activated on the
Plugins page. The download/validate/cache logic lives in
:func:`mission_deck.config.fetch_remote_config` (stdlib ``urllib``); this view
runs it off the Tk thread via ``app.run_background`` and is otherwise pure
presentation. Every sync attempt is audited (``cloud.sync``).
"""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk
from tkinter import messagebox

from mission_deck import __app_name__
from mission_deck.config import cloud_config_path, fetch_remote_config
from mission_deck.icons import icon
from mission_deck.logging_setup import audit
from mission_deck.theme import (
    COLORS,
    CORNER,
    CORNER_LG,
    GAP,
    PAD,
)
from mission_deck.ui import BTN_OUTLINE, BTN_SOLID, font

_FETCH_TIMEOUT_S = 15.0

_FEATURES = [
    ("Any HTTPS source", "OneDrive / SharePoint direct links, an intranet "
     "server, a Git raw URL — anything that serves the JSON works."),
    ("Offline-safe", "Each sync is cached locally. If the source is "
     "unreachable, mission-deck keeps running on the last-good copy."),
    ("Validated & audited", "Downloads are schema-checked before they can "
     "replace the cache, and every sync lands in the audit log."),
]


class CloudView(ctk.CTkScrollableFrame):
    """Sync ``config.json`` from a central HTTPS URL to every workstation."""

    def __init__(self, master, app: "App"):  # noqa: F821 - App imported lazily
        super().__init__(master, fg_color="transparent")
        self.app = app
        self._syncing = False
        self.grid_columnconfigure(0, weight=1)
        self._build_hero()
        self._build_feature_cards()
        self._refresh_status()

    # ------------------------------------------------------------------ #
    def _build_hero(self) -> None:
        hero = ctk.CTkFrame(
            self, corner_radius=CORNER_LG, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        hero.grid(row=0, column=0, sticky="ew")
        hero.grid_columnconfigure(0, weight=1)

        inner = ctk.CTkFrame(hero, fg_color="transparent")
        inner.grid(row=0, column=0, pady=(36, 8))
        ctk.CTkLabel(
            inner, text="", image=icon("cloud", 44, COLORS["text_muted"]),
        ).pack()
        ctk.CTkLabel(
            inner, text="One config for every workstation",
            font=font(18, weight="bold"), text_color=COLORS["text"],
        ).pack(pady=(10, 2))
        ctk.CTkLabel(
            inner, text="Paste the HTTPS URL your estate's config.json is "
            "published at, then Sync. The file is validated, cached locally "
            "and loaded from the cache.",
            anchor="center", justify="center", wraplength=560,
            font=font(12), text_color=COLORS["text_muted"],
        ).pack()

        # URL entry + sync action.
        form = ctk.CTkFrame(hero, fg_color="transparent")
        form.grid(row=1, column=0, sticky="ew", padx=PAD * 2, pady=(8, 4))
        form.grid_columnconfigure(0, weight=1)
        self._url_var = ctk.StringVar(value=self.app.app_state.cloud_config_url)
        ctk.CTkEntry(
            form, textvariable=self._url_var, height=36, corner_radius=CORNER,
            placeholder_text="https://…/config.json",
            border_width=1, border_color=COLORS["border_2"], fg_color=COLORS["card_2"],
            font=font(12, mono=True), text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="ew")
        self._sync_btn = ctk.CTkButton(
            form, text="SYNC NOW", width=120, height=36,
            font=font(11, mono=True, weight="bold"), command=self._sync,
            **BTN_SOLID,
        )
        self._sync_btn.grid(row=0, column=1, padx=(GAP, 0))
        self._use_cache_btn = ctk.CTkButton(
            form, text="USE CACHED COPY", width=150, height=36,
            font=font(11, mono=True), command=self._use_cached,
            **BTN_OUTLINE,
        )
        self._use_cache_btn.grid(row=0, column=2, padx=(GAP, 0))

        # Status line: cache state + last sync + estate that would load.
        self._status = ctk.CTkLabel(
            hero, text="", anchor="center",
            font=font(11, mono=True), text_color=COLORS["text_faint"],
        )
        self._status.grid(row=2, column=0, pady=(2, 28))

    def _build_feature_cards(self) -> None:
        cards = ctk.CTkFrame(self, fg_color="transparent")
        cards.grid(row=1, column=0, sticky="ew", pady=(GAP, 0))
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

    # ------------------------------------------------------------------ #
    def _refresh_status(self) -> None:
        cache = cloud_config_path()
        bits: list[str] = []
        if cache.is_file():
            loaded = self.app.config_path is not None and self.app.config_path == cache
            bits.append("cache: loaded" if loaded else f"cache: {cache.name}")
            self._use_cache_btn.configure(state="normal")
        else:
            bits.append("no cached copy yet")
            self._use_cache_btn.configure(state="disabled")
        if self.app.app_state.cloud_last_sync:
            bits.append(f"last sync {self.app.app_state.cloud_last_sync}")
        self._status.configure(text="   ·   ".join(bits))

    def _sync(self) -> None:
        if self._syncing:
            return
        url = self._url_var.get().strip()
        if not url:
            messagebox.showerror(__app_name__, "Enter the URL your config.json is published at.", parent=self)
            return
        self._syncing = True
        self._sync_btn.configure(state="disabled", text="SYNCING…")

        def work():
            return fetch_remote_config(url, timeout=_FETCH_TIMEOUT_S)

        def done(ok: bool, result) -> None:
            self._syncing = False
            self._sync_btn.configure(state="normal", text="SYNC NOW")
            audit("cloud.sync", url=url, ok=ok, **({} if ok else {"error": str(result)}))
            if not ok:
                self.app.toaster.show("Cloud sync failed", str(result), kind="error")
                self._refresh_status()
                return
            state = self.app.app_state
            state.cloud_config_url = url
            state.cloud_last_sync = datetime.now().strftime("%Y-%m-%d %H:%M")
            state.save()
            self.app.toaster.show("Config synced", f"Cached at {result}", kind="success")
            self._refresh_status()
            if messagebox.askyesno(
                __app_name__,
                "Config synced and validated.\n\nLoad it now? "
                "(mission-deck will reload onto the synced estate.)",
                parent=self,
            ):
                self._load(result)

        self.app.run_background(work, done)

    def _use_cached(self) -> None:
        cache = cloud_config_path()
        if not cache.is_file():
            return
        if messagebox.askyesno(
            __app_name__,
            "Load the locally cached cloud config? "
            "(mission-deck will reload onto the cached estate.)",
            parent=self,
        ):
            self._load(cache)

    def _load(self, path) -> None:
        """Soft-restart the app onto ``path`` (same flow as the config picker)."""

        self.app.requested_config = path
        self.app.destroy()
