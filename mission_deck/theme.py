"""Nothing-inspired design tokens for mission-deck.

Dark mode: OLED black canvas, white data glowing like an instrument panel.
Monochrome hierarchy via gray scale; accent red (#D71921) is an interrupt —
it appears only when a device is offline or an action is destructive.

Fonts (design intent — install from Google Fonts for the real thing):
  Space Grotesk  →  UI / body (system fallback: Segoe UI via CTk default)
  Space Mono     →  data, labels, ALL CAPS metadata (system fallback: Consolas)
  Doto           →  hero numbers only (system fallback: Space Mono / Consolas)
"""

from __future__ import annotations

from mission_deck.models import DeviceStatus, RecordingStatus

# --------------------------------------------------------------------------- #
# Palette — Nothing dark mode
#
# Four text tiers (100 → 90 → 60 → 40%) map to the gray scale.
# Red is not part of the hierarchy — it is an event.
# --------------------------------------------------------------------------- #
COLORS: dict[str, str] = {
    # Surfaces
    "bg":            "#000000",  # OLED black
    "rail":          "#111111",  # top navigation bar
    "panel":         "#111111",  # main content panel
    "header":        "#111111",  # top bar (alias of panel)
    "sidebar":       "#111111",  # room-list sidebar
    "card":          "#1A1A1A",  # card surface (--surface-raised)
    "card_2":        "#222222",  # secondary / inset fill
    "card_hover":    "#222222",  # hover fill
    "elev":          "#222222",  # elevated surfaces (menus, overlays)
    "pill":          "#1A1A1A",  # chip / tag background

    # Borders (decorative → intentional)
    "border":        "#222222",  # hairline, decorative
    "border_2":      "#333333",  # intentional / interactive
    "border_strong": "#333333",  # hover / focus ring

    # Text hierarchy (100% → 60% → 40%)
    "text":          "#E8E8E8",  # primary body text          (~90%)
    "text_muted":    "#999999",  # labels, captions           (~60%)
    "text_faint":    "#666666",  # metadata, hints            (~40%)
    "ghost":         "#333333",  # decorative glyphs

    # Accent — signal red. One use per screen: errors / offline / destructive.
    "accent":        "#D71921",
    "accent_hover":  "#B5161B",
    "accent_press":  "#8E1015",
    "accent_soft":   "#1A0000",  # very dark tint for selected items
    "accent_line":   "#3D0709",  # accent-tinted border
    "accent_text":   "#E05257",  # accent text on dark bg

    # Status semantics — data encoding only (value, not label or row bg)
    "online":        "#4A9E5C",  # connected / success
    "offline":       "#D71921",  # unreachable / error (shares accent)
    "warn":          "#D4A843",  # degraded / checking
    "info":          "#999999",  # informational — uses secondary text color
    "rec":           "#D71921",  # "on air" recording (shares accent)
    "unknown":       "#666666",  # never checked
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
