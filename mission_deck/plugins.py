"""The Plugins / integrations view.

A GUI surface over the registries that already exist in the codebase —
**monitors** (how a device's reachability is judged, see
``network.py``'s monitor registry), **device commands**, and the conceptual
**config sources** & **notifiers**. Each real extension is a single decorator
away; this screen is where an operator enables and configures them.

This is a presentation-only placeholder: the toggle switches and *Configure*
buttons are not yet wired to live plugin loading. Built-in monitors (tcp /
http) are surfaced as already-enabled so the page reflects reality; everything
else is a "coming soon" concept card.
"""

from __future__ import annotations

import customtkinter as ctk

from mission_deck.icons import icon
from mission_deck.theme import (
    COLORS,
    CORNER,
    CORNER_LG,
    GAP,
    PAD,
)

# Each entry: (name, author, icon, accent, enabled, badge, description, tags)
PLUGINS: list[dict] = [
    {
        "name": "OneDrive Config Sync", "by": "mission-deck core", "icon": "cloud",
        "accent": "#2b9be6", "enabled": True, "badge": "Enabled",
        "desc": "Store config.json in OneDrive / SharePoint so every operator "
                "workstation loads the same estate. Background pull, conflict-"
                "aware push, revision history.",
        "tags": ["config", "cloud", "sync"],
    },
    {
        "name": "TCP Monitor", "by": "mission-deck core", "icon": "pulse",
        "accent": COLORS["accent"], "enabled": True, "badge": "Built-in",
        "desc": "Default reachability check — opens a TCP connection to the "
                "control port. Bounded concurrency for estate-wide sweeps.",
        "tags": ["monitor"],
    },
    {
        "name": "HTTP / HTTPS Monitor", "by": "mission-deck core", "icon": "link",
        "accent": COLORS["online"], "enabled": True, "badge": "Built-in",
        "desc": "Treats any answered HTTP(S) endpoint as up. Opt in per device "
                "with a monitor key; probes health_url.",
        "tags": ["monitor", "http"],
    },
    {
        "name": "Slack Alerts", "by": "community", "icon": "bell",
        "accent": "#8a6fbf", "enabled": False, "badge": "Available",
        "desc": "Post to a Slack channel when a device goes offline, a recorder "
                "stops mid-session, or a sweep finds new failures.",
        "tags": ["notify", "webhook"],
    },
    {
        "name": "PagerDuty", "by": "community", "icon": "warn",
        "accent": "#3fb950", "enabled": False, "badge": "Available",
        "desc": "Open and resolve incidents from device-down events. Severity "
                "mapping per room and per device category.",
        "tags": ["notify", "on-call"],
    },
    {
        "name": "SNMP Monitor", "by": "community", "icon": "server",
        "accent": COLORS["warn"], "enabled": False, "badge": "Beta",
        "desc": "Judge reachability and pull health OIDs (temperature, PSU, "
                "fan) from network switches and matrix hardware.",
        "tags": ["monitor", "snmp"],
    },
    {
        "name": "Crestron Fusion", "by": "community", "icon": "cpu",
        "accent": "#22b8cf", "enabled": False, "badge": "Available",
        "desc": "Cross-reference room status with Crestron Fusion scheduling so "
                "a courtroom shows its next listed session.",
        "tags": ["integration"],
    },
    {
        "name": "Webhook Commands", "by": "community", "icon": "command",
        "accent": COLORS["info"], "enabled": False, "badge": "Available",
        "desc": "Expose device commands as outbound webhooks so external "
                "systems can trigger presets, routes, and power actions.",
        "tags": ["automation", "webhook"],
    },
]

_PLUGIN_COLUMNS = 2


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
    """A small pill with a knob that *looks* like a toggle (decorative for now)."""

    def __init__(self, master, on: bool):
        super().__init__(
            master, width=38, height=20, corner_radius=10,
            fg_color=COLORS["accent"] if on else COLORS["border_2"],
        )
        self.grid_propagate(False)
        knob = ctk.CTkFrame(self, width=14, height=14, corner_radius=7, fg_color="#ffffff")
        knob.place(relx=1.0 if on else 0.0, rely=0.5, anchor="e" if on else "w",
                   x=-3 if on else 3)


class PluginCard(ctk.CTkFrame):
    """One marketplace card describing an extension."""

    def __init__(self, master, spec: dict):
        super().__init__(
            master, corner_radius=CORNER_LG, fg_color=COLORS["card"],
            border_width=1, border_color=COLORS["border"],
        )
        self.grid_columnconfigure(1, weight=1)

        # Icon tile.
        tile = ctk.CTkFrame(
            self, width=42, height=42, corner_radius=10,
            fg_color=COLORS["card_2"], border_width=1, border_color=COLORS["border"],
        )
        tile.grid(row=0, column=0, rowspan=2, padx=(PAD, GAP), pady=(PAD, 0), sticky="n")
        tile.grid_propagate(False)
        ctk.CTkLabel(
            tile, text="", image=icon(spec["icon"], 22, spec["accent"]),
        ).place(relx=0.5, rely=0.5, anchor="center")

        # Title + badge.
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=1, sticky="ew", padx=(0, PAD), pady=(PAD, 0))
        ctk.CTkLabel(
            head, text=spec["name"], anchor="w",
            font=ctk.CTkFont(size=14, weight="bold"), text_color=COLORS["text"],
        ).pack(side="left")
        badge = spec["badge"]
        on = spec["enabled"]
        badge_fg = COLORS["accent_soft"] if badge == "Beta" else (
            "#143524" if on else COLORS["card_2"])
        badge_tc = COLORS["accent_text"] if badge == "Beta" else (
            COLORS["online"] if on else COLORS["text_faint"])
        ctk.CTkLabel(
            head, text=f" {badge} ", corner_radius=8, height=18,
            font=ctk.CTkFont(size=10, weight="bold"),
            fg_color=badge_fg, text_color=badge_tc,
        ).pack(side="left", padx=(8, 0))
        _Switch(head, on).pack(side="right")

        ctk.CTkLabel(
            self, text=f"by {spec['by']}", anchor="w",
            font=ctk.CTkFont(size=11, family="Consolas"), text_color=COLORS["text_faint"],
        ).grid(row=1, column=1, sticky="ew", padx=(0, PAD))

        # Description.
        ctk.CTkLabel(
            self, text=spec["desc"], anchor="w", justify="left", wraplength=380,
            font=ctk.CTkFont(size=12), text_color=COLORS["text_muted"],
        ).grid(row=2, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(GAP, 0))

        # Footer: tags + Configure.
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=3, column=0, columnspan=2, sticky="ew", padx=PAD, pady=(GAP, PAD))
        foot.grid_columnconfigure(0, weight=1)
        tags = ctk.CTkFrame(foot, fg_color="transparent")
        tags.grid(row=0, column=0, sticky="w")
        for tag in spec["tags"]:
            ctk.CTkLabel(
                tags, text=f" {tag} ", corner_radius=4, height=18,
                font=ctk.CTkFont(size=10, family="Consolas"),
                fg_color=COLORS["card_2"], text_color=COLORS["text_muted"],
            ).pack(side="left", padx=(0, 5))
        ctk.CTkButton(
            foot, text="Configure", image=icon("settings", 14, COLORS["text_muted"]),
            compound="left", width=104, height=26, corner_radius=CORNER,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"], text_color=COLORS["text_muted"],
            font=ctk.CTkFont(size=12), command=lambda: None,
        ).grid(row=0, column=1, sticky="e")


class PluginsView(ctk.CTkScrollableFrame):
    """The Plugins marketplace screen (presentation-only placeholder)."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent")
        self.grid_columnconfigure(0, weight=1)

        note = spec_note(
            self,
            "Plugins extend three registries already in the codebase — monitors "
            "(how reachability is judged), device commands, and the planned config "
            "sources & notifiers. Each is a single decorator away; this page is "
            "the GUI surface for enabling and configuring them.  (Coming soon — "
            "the marketplace below is a concept preview.)",
        )
        note.grid(row=0, column=0, sticky="ew", pady=(0, GAP))

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="nsew")
        for col in range(_PLUGIN_COLUMNS):
            grid.grid_columnconfigure(col, weight=1, uniform="plug")
        for index, spec in enumerate(PLUGINS):
            row, col = divmod(index, _PLUGIN_COLUMNS)
            card = PluginCard(grid, spec)
            card.grid(
                row=row, column=col, sticky="nsew",
                padx=(0 if col == 0 else GAP // 2, 0 if col == _PLUGIN_COLUMNS - 1 else GAP // 2),
                pady=(0, GAP),
            )
