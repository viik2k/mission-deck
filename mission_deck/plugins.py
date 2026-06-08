"""The Plugins / integrations view + the plugin registry.

A GUI surface over the registries that already exist in the codebase —
**monitors** (how a device's reachability is judged, see ``network.py``'s
monitor registry), **device commands**, and the conceptual **config sources**
& **notifiers**. Each real extension is a single decorator away; this screen is
where an operator enables and configures them.

This is intentionally a *preview*, not a finished marketplace: only the
mechanics that drive the rest of the app are wired up. Activating a plugin that
contributes a nav tile (today: **Cloud Sync**) makes that tile appear in the
left rail — that is the "downloaded plugins show up in the sidebar" behaviour.
Everything else is eye candy with a "coming soon" badge so the shape of the
final screen is visible without pretending the integrations exist yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import customtkinter as ctk

from mission_deck.icons import icon
from mission_deck.theme import (
    COLORS,
    CORNER,
    CORNER_LG,
    GAP,
    PAD,
)


@dataclass(frozen=True)
class PluginSpec:
    """Static description of a plugin shown on the Plugins screen.

    ``tile`` — when set, ``(view_key, label, icon)`` for a rail button that
    appears only while the plugin is enabled. ``builtin`` plugins are always on
    and cannot be toggled (the tcp/http monitors that already ship).
    """

    id: str
    name: str
    by: str
    icon: str
    accent: str
    desc: str
    tags: list[str] = field(default_factory=list)
    builtin: bool = False
    tile: tuple[str, str, str] | None = None
    badge: str = "Available"


# The plugin catalogue. Today this is the source of truth for both the Plugins
# screen and the rail tiles; a real loader would discover these instead.
PLUGINS: list[PluginSpec] = [
    PluginSpec(
        id="cloud_sync", name="Cloud Sync", by="mission-deck core", icon="cloud",
        accent="#2b9be6",
        desc="Store config.json in OneDrive / SharePoint so every operator "
             "workstation loads the same estate.",
        tags=["config", "cloud"], tile=("cloud", "Cloud Sync", "cloud"),
    ),
    PluginSpec(
        id="monitor_tcp", name="TCP Monitor", by="mission-deck core", icon="pulse",
        accent=COLORS["accent"], builtin=True, badge="Built-in",
        desc="Default reachability check — opens a TCP connection to the "
             "control port.",
        tags=["monitor"],
    ),
    PluginSpec(
        id="monitor_http", name="HTTP / HTTPS Monitor", by="mission-deck core",
        icon="link", accent=COLORS["online"], builtin=True, badge="Built-in",
        desc="Treats any answered HTTP(S) endpoint as up. Opt in per device.",
        tags=["monitor"],
    ),
]

_PLUGIN_COLUMNS = 2


def plugin_by_id(plugin_id: str) -> PluginSpec | None:
    for spec in PLUGINS:
        if spec.id == plugin_id:
            return spec
    return None


def tile_plugins() -> list[PluginSpec]:
    """Plugins that contribute a rail tile when enabled."""

    return [spec for spec in PLUGINS if spec.tile is not None]


def spec_note(master, text: str) -> ctk.CTkFrame:
    """A dashed accent banner used to annotate a view for the developer spec."""

    frame = ctk.CTkFrame(
        master, corner_radius=CORNER, fg_color=COLORS["accent_soft"],
        border_width=1, border_color=COLORS["accent_line"],
    )
    frame.grid_columnconfigure(1, weight=1)
    ctk.CTkLabel(
        frame, text="", image=icon("bolt", 15, COLORS["accent_text"]),
    ).grid(row=0, column=0, padx=(12, 6), pady=10, sticky="n")
    ctk.CTkLabel(
        frame, text=text, anchor="w", justify="left", wraplength=1100,
        font=ctk.CTkFont(size=12), text_color=COLORS["accent_text"],
    ).grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=10)
    return frame


class _Switch(ctk.CTkFrame):
    """A small pill toggle. Interactive when ``command`` is given, else static."""

    def __init__(self, master, on: bool, command=None):
        self._on = on
        self._command = command
        super().__init__(
            master, width=38, height=20, corner_radius=10,
            fg_color=COLORS["accent"] if on else COLORS["border_2"],
        )
        self.grid_propagate(False)
        self._knob = ctk.CTkFrame(self, width=14, height=14, corner_radius=7, fg_color="#ffffff")
        self._knob.place(relx=1.0 if on else 0.0, rely=0.5, anchor="e" if on else "w",
                         x=-3 if on else 3)
        if command is not None:
            self.configure(cursor="hand2")
            for widget in (self, self._knob):
                widget.bind("<Button-1>", self._clicked)

    def _clicked(self, _event=None) -> None:
        if self._command is not None:
            self._command(not self._on)


class PluginCard(ctk.CTkFrame):
    """One card describing an extension and (for togglable plugins) its switch."""

    def __init__(self, master, spec: PluginSpec, *, enabled: bool, on_toggle=None):
        super().__init__(
            master, corner_radius=CORNER_LG, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        self.grid_columnconfigure(1, weight=1)
        togglable = on_toggle is not None and not spec.builtin

        # Icon tile.
        tile = ctk.CTkFrame(
            self, width=42, height=42, corner_radius=10,
            fg_color=COLORS["card_2"], border_width=1, border_color=COLORS["border"],
        )
        tile.grid(row=0, column=0, rowspan=2, padx=(PAD, GAP), pady=(PAD, 0), sticky="n")
        tile.grid_propagate(False)
        ctk.CTkLabel(
            tile, text="", image=icon(spec.icon, 22, spec.accent),
        ).place(relx=0.5, rely=0.5, anchor="center")

        # Title + badge + switch.
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=1, sticky="ew", padx=(0, PAD), pady=(PAD, 0))
        ctk.CTkLabel(
            head, text=spec.name, anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=COLORS["text"],
        ).pack(side="left")
        self._badge_pill(head, spec, enabled).pack(side="left", padx=(8, 0))
        if spec.builtin:
            _Switch(head, True).pack(side="right")
        elif togglable:
            _Switch(head, enabled, command=lambda on: on_toggle(spec.id, on)).pack(side="right")

        ctk.CTkLabel(
            self, text=f"by {spec.by}", anchor="w",
            font=ctk.CTkFont(size=11, family="Consolas"), text_color=COLORS["text_faint"],
        ).grid(row=1, column=1, sticky="ew", padx=(0, PAD))

        # Description.
        ctk.CTkLabel(
            self, text=spec.desc, anchor="w", justify="left", wraplength=380,
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(GAP, PAD))

    def _badge_pill(self, master, spec: PluginSpec, enabled: bool) -> ctk.CTkLabel:
        if spec.builtin:
            text, fg, tc = "Built-in", COLORS["card_2"], COLORS["online"]
        elif enabled:
            text, fg, tc = "Active", "#143524", COLORS["online"]
        elif spec.badge == "Coming soon":
            text, fg, tc = "Coming soon", COLORS["card_2"], COLORS["text_faint"]
        else:
            text, fg, tc = "Available", COLORS["accent_soft"], COLORS["accent_text"]
        return ctk.CTkLabel(
            master, text=f" {text} ", corner_radius=8, height=18,
            font=ctk.CTkFont(size=10, weight="bold"), fg_color=fg, text_color=tc,
        )


class PluginsView(ctk.CTkScrollableFrame):
    """The Plugins screen — eye-candy preview with working activation toggles."""

    def __init__(self, master, app: "App"):  # noqa: F821 - App imported lazily
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.grid_columnconfigure(0, weight=1)
        self._build()

    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        self._hero().grid(row=0, column=0, sticky="ew", pady=(0, GAP))

        note = spec_note(
            self,
            "Plugins extend three registries already in the codebase — monitors, "
            "device commands, and the planned config sources & notifiers. Activate "
            "a plugin to drop its tile into the rail; the marketplace below is a "
            "preview.  (Coming soon.)",
        )
        note.grid(row=1, column=0, sticky="ew", pady=(0, GAP))

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.grid(row=2, column=0, sticky="nsew")
        for col in range(_PLUGIN_COLUMNS):
            grid.grid_columnconfigure(col, weight=1, uniform="plug")
        for index, spec in enumerate(PLUGINS):
            row, col = divmod(index, _PLUGIN_COLUMNS)
            card = PluginCard(
                grid, spec,
                enabled=self.app.is_plugin_enabled(spec.id),
                on_toggle=self._on_toggle,
            )
            card.grid(
                row=row, column=col, sticky="nsew",
                padx=(0 if col == 0 else GAP // 2, 0 if col == _PLUGIN_COLUMNS - 1 else GAP // 2),
                pady=(0, GAP),
            )

    def _hero(self) -> ctk.CTkFrame:
        hero = ctk.CTkFrame(
            self, corner_radius=CORNER_LG, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        hero.grid_columnconfigure(1, weight=1)
        tile = ctk.CTkFrame(
            hero, width=52, height=52, corner_radius=14, fg_color=COLORS["accent_soft"],
            border_width=1, border_color=COLORS["accent_line"],
        )
        tile.grid(row=0, column=0, rowspan=2, padx=(PAD, GAP), pady=PAD, sticky="n")
        tile.grid_propagate(False)
        ctk.CTkLabel(
            tile, text="", image=icon("plugins", 26, COLORS["accent_text"]),
        ).place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(
            hero, text="Extend mission-deck", anchor="w",
            font=ctk.CTkFont(size=18, weight="bold"), text_color=COLORS["text"],
        ).grid(row=0, column=1, sticky="ew", padx=(0, PAD), pady=(PAD, 0))
        ctk.CTkLabel(
            hero, text="Activate a plugin to add monitors, notifiers and config "
            "sources. Tiles for what you turn on appear in the rail.",
            anchor="w", justify="left", wraplength=720,
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
        ).grid(row=1, column=1, sticky="ew", padx=(0, PAD), pady=(2, PAD))
        return hero

    # ------------------------------------------------------------------ #
    def _on_toggle(self, plugin_id: str, enabled: bool) -> None:
        self.app.set_plugin_enabled(plugin_id, enabled)
        self.refresh()

    def refresh(self) -> None:
        """Rebuild the cards so badges/switches reflect current enablement."""

        for child in self.winfo_children():
            child.destroy()
        self._build()
