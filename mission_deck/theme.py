"""Design tokens for mission-deck.

Dark mode: a near-black canvas with data glowing like an instrument panel.
Surfaces are a **cool-charcoal elevation ladder** — each plane (canvas → nav →
sidebar → panel → card → inset) is a distinct, faintly blue-tinted step, so the
UI reads as layered depth rather than one flat grey.

Two accents, two jobs:
  * **Red** (#D71921) is an *interrupt* — alerts, offline devices, destructive
    actions. It means "something is wrong / be careful".
  * **Azure** (#4E8FEA) is *orientation* — the active view, the selected room,
    focus rings, informational signals. It means "you are here / interactive".

Keeping the two roles separate is what stops red from being diluted: when red
appears it still means trouble, because everyday interactive emphasis is azure.

Fonts (design intent — install from Google Fonts for the real thing):
  Space Grotesk  →  UI / body (system fallback: Segoe UI via CTk default)
  Space Mono     →  data, labels, ALL CAPS metadata (system fallback: Consolas)
  Doto           →  hero numbers only (system fallback: Space Mono / Consolas)
"""

from __future__ import annotations

from mission_deck.models import DeviceStatus, RecordingStatus

# --------------------------------------------------------------------------- #
# Palette — cool-charcoal dark mode
#
# Neutrals carry a faint blue cast (hue ~220°, very low saturation) so the dark
# planes feel like graphite, not dead grey. Four text tiers (100 → 90 → 60 →
# 40%) map down the ladder. Red and azure are accents, not part of the ramp.
# --------------------------------------------------------------------------- #
COLORS: dict[str, str] = {
    # Surfaces — a genuine elevation ladder (canvas darkest → inset lightest)
    "bg":            "#000000",  # OLED black canvas
    "rail":          "#0C0E13",  # top navigation bar (raised band)
    "panel":         "#0F1218",  # main content panel
    "header":        "#0C0E13",  # top bar (alias of rail)
    "sidebar":       "#0A0C10",  # room-list sidebar (recessed)
    "card":          "#171B22",  # card surface (--surface-raised)
    "card_2":        "#1F242E",  # secondary / inset fill
    "card_hover":    "#232A35",  # hover fill (a step above card_2)
    "elev":          "#1F242E",  # elevated surfaces (menus, overlays)
    "pill":          "#171B22",  # chip / tag background

    # Borders (decorative → intentional)
    "border":        "#222831",  # hairline, decorative
    "border_2":      "#2D3440",  # intentional / interactive
    "border_strong": "#3A4250",  # hover / focus ring

    # Text hierarchy (100% → 60% → 40%) — a hair cool to match the surfaces
    "text":          "#E8EAEF",  # primary body text          (~90%)
    "text_muted":    "#9AA1AD",  # labels, captions           (~60%)
    "text_faint":    "#646B78",  # metadata, hints            (~40%)
    "ghost":         "#2D3440",  # decorative glyphs

    # Accent — signal red. Errors / offline / destructive only.
    "accent":        "#D71921",
    "accent_hover":  "#B5161B",
    "accent_press":  "#8E1015",
    "accent_soft":   "#1E0A0C",  # very dark red tint for alert backgrounds
    "accent_line":   "#43090C",  # accent-tinted border
    "accent_text":   "#E8595E",  # accent text on dark bg

    # Accent 2 — orientation azure. Active view / selection / focus / info.
    "accent2":       "#4E8FEA",
    "accent2_hover": "#6AA2EE",
    "accent2_soft":  "#0E1A2A",  # dark azure tint for active / selected fills
    "accent2_line":  "#284665",  # azure-tinted border
    "accent2_text":  "#8FBEF4",  # azure text on dark bg

    # Status semantics — data encoding only (value, not label or row bg)
    "online":        "#4DA75E",  # connected / success
    "offline":       "#D71921",  # unreachable / error (shares accent)
    "warn":          "#D9A441",  # degraded / checking
    "info":          "#4E8FEA",  # informational (shares orientation azure)
    "rec":           "#D71921",  # "on air" recording (shares accent)
    "unknown":       "#646B78",  # never checked

    # Soft status fills — faint washes so a state can colour a whole row/card,
    # not just a dot. Tuned to sit one step above their plane on the ladder.
    "online_soft":   "#0D1A12",  # faint green wash
    "offline_soft":  "#1E0C0E",  # faint red wash (offline cards)
    "warn_soft":     "#1C1707",  # faint amber wash
}

# Status indicator colours keyed by DeviceStatus.
STATUS_COLORS: dict[DeviceStatus, str] = {
    DeviceStatus.ONLINE:   COLORS["online"],
    DeviceStatus.OFFLINE:  COLORS["offline"],
    DeviceStatus.CHECKING: COLORS["warn"],
    DeviceStatus.UNKNOWN:  COLORS["unknown"],
}

STATUS_LABELS: dict[DeviceStatus, str] = {
    DeviceStatus.ONLINE:   "Online",
    DeviceStatus.OFFLINE:  "Offline",
    DeviceStatus.CHECKING: "Checking…",
    DeviceStatus.UNKNOWN:  "Unknown",
}


def status_color(status: DeviceStatus) -> str:
    return STATUS_COLORS.get(status, STATUS_COLORS[DeviceStatus.UNKNOWN])


def status_label(status: DeviceStatus) -> str:
    return STATUS_LABELS.get(status, "Unknown")


# Recording status colours and labels.
RECORDING_STATUS_COLORS: dict[RecordingStatus, str] = {
    RecordingStatus.RECORDING: COLORS["rec"],
    RecordingStatus.PAUSED:    COLORS["warn"],
    RecordingStatus.IDLE:      COLORS["unknown"],
    RecordingStatus.UNKNOWN:   COLORS["text_faint"],
}

RECORDING_STATUS_LABELS: dict[RecordingStatus, str] = {
    RecordingStatus.RECORDING: "● REC",
    RecordingStatus.PAUSED:    "◐ PAUSED",
    RecordingStatus.IDLE:      "○ IDLE",
    RecordingStatus.UNKNOWN:   "○ UNKNOWN",
}


def recording_status_color(status: RecordingStatus) -> str:
    return RECORDING_STATUS_COLORS.get(status, RECORDING_STATUS_COLORS[RecordingStatus.UNKNOWN])


def recording_status_label(status: RecordingStatus) -> str:
    return RECORDING_STATUS_LABELS.get(status, "Unknown")


# KPI accents (reuse status palette for consistency).
KPI_GOOD    = COLORS["online"]
KPI_BAD     = COLORS["offline"]
KPI_NEUTRAL = COLORS["text_muted"]


# --------------------------------------------------------------------------- #
# Typography
# --------------------------------------------------------------------------- #
# Monospace family: Space Mono design intent; Consolas as Windows system fallback.
FONT_MONO = "Consolas"

# UI / body family: Space Grotesk design intent; CTk default (Roboto) is fine fallback.
# Specify explicitly when CTk's default isn't geometric enough.
FONT_UI = "Segoe UI"


# --------------------------------------------------------------------------- #
# Sizing / layout tokens
# --------------------------------------------------------------------------- #
NAV_HEIGHT    = 52             # top navigation bar (brand + tabs + global actions)
SIDEBAR_WIDTH = 240            # room-list sidebar
TOPBAR_HEIGHT = 44             # context bar (breadcrumb + per-view actions)
CARD_MIN_WIDTH = 280
CARD_HEIGHT   = 88             # compact device tile
GRID_COLUMNS  = 3
PAD           = 16             # standard outer padding
GAP           = 12             # standard inter-widget gap
CORNER        = 8              # standard corner radius (cards)
CORNER_LG     = 12             # larger (panels, overlays)
