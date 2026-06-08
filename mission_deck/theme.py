"""Central colour palette and sizing tokens for the mission-deck UI.

Keeping these in one place gives the whole app a consistent, enterprise-grade
dark look and makes re-theming a one-file change. The palette is a modern
"observability console" — a deep charcoal slate with a single blue accent, so
the status indicators (green/red/amber) pop. Data (IPs, ports, latency) is set
in a monospace family; everything else uses the UI sans.

Colours are deliberately low-saturation across several tiers (panel → card →
elevated) so panels read as layered surfaces without heavy borders.
"""

from __future__ import annotations

from mission_deck.models import DeviceStatus, RecordingStatus

# --------------------------------------------------------------------------- #
# Palette (dark) — modern charcoal slate observability console
#
# Surfaces are layered from deepest (app background) to most elevated
# (pop-overs). Several keys are aliases kept for backwards compatibility with
# code that pre-dates the redesign (e.g. ``header`` == topbar background).
# --------------------------------------------------------------------------- #
COLORS: dict[str, str] = {
    # Surfaces — deep charcoal slate, layered.
    "bg": "#0a0c10",            # app background (behind everything)
    "rail": "#0b0d11",          # left icon rail
    "panel": "#0d1014",         # main content column
    "header": "#0d1014",        # top bar (alias of panel)
    "sidebar": "#0e1116",       # room-list sidebar (inside Rooms view)
    "card": "#14181f",          # card / panel fill
    "card_2": "#181d25",        # secondary fill (pills, inset stats)
    "card_hover": "#1b212b",    # card / row hover
    "elev": "#1d232d",          # elevated surfaces (tooltips, menus)
    "pill": "#181d25",          # category pill background (alias of card_2)

    # Borders — three tiers from hairline to strong.
    "border": "#232a34",        # hairline separators / card outlines
    "border_2": "#2c343f",      # slightly stronger
    "border_strong": "#38414e", # hover / focus outline

    # Text.
    "text": "#e7e9ee",          # primary text
    "text_muted": "#9099a6",    # secondary text
    "text_faint": "#626b78",    # tertiary / metadata
    "ghost": "#444c58",         # quaternary / decorative glyphs

    # Accent (blue — keeps the existing brand).
    "accent": "#3b82f6",        # brand / primary action
    "accent_hover": "#2f6fe0",
    "accent_press": "#2a5cb8",
    "accent_soft": "#16243f",   # selected item / soft fill
    "accent_line": "#2a3f63",   # accent-tinted border
    "accent_text": "#93b4f7",   # accent text on dark

    # Status semantics (kept constant across any accent change).
    "online": "#2ecc71",        # reachable / success
    "offline": "#ef4444",       # unreachable / error
    "warn": "#f5a623",          # degraded / checking
    "info": "#38bdf8",          # informational (sync source, etc.)
    "rec": "#ff4d4f",           # "on air" recording red
    "unknown": "#6b7280",       # never checked
}

# Status indicator colours, keyed by DeviceStatus.
STATUS_COLORS: dict[DeviceStatus, str] = {
    DeviceStatus.ONLINE: COLORS["online"],
    DeviceStatus.OFFLINE: COLORS["offline"],
    DeviceStatus.CHECKING: COLORS["warn"],
    DeviceStatus.UNKNOWN: COLORS["unknown"],
}

STATUS_LABELS: dict[DeviceStatus, str] = {
    DeviceStatus.ONLINE: "Online",
    DeviceStatus.OFFLINE: "Offline",
    DeviceStatus.CHECKING: "Checking…",
    DeviceStatus.UNKNOWN: "Unknown",
}


def status_color(status: DeviceStatus) -> str:
    return STATUS_COLORS.get(status, STATUS_COLORS[DeviceStatus.UNKNOWN])


def status_label(status: DeviceStatus) -> str:
    return STATUS_LABELS.get(status, "Unknown")


# Recording status colours and labels.
RECORDING_STATUS_COLORS: dict[RecordingStatus, str] = {
    RecordingStatus.RECORDING: COLORS["rec"],   # red — "on air" convention
    RecordingStatus.PAUSED: COLORS["warn"],     # amber
    RecordingStatus.IDLE: COLORS["unknown"],    # gray
    RecordingStatus.UNKNOWN: COLORS["text_faint"],
}

RECORDING_STATUS_LABELS: dict[RecordingStatus, str] = {
    RecordingStatus.RECORDING: "● Recording",
    RecordingStatus.PAUSED: "◐ Paused",
    RecordingStatus.IDLE: "○ Idle",
    RecordingStatus.UNKNOWN: "○ Unknown",
}


def recording_status_color(status: RecordingStatus) -> str:
    return RECORDING_STATUS_COLORS.get(status, RECORDING_STATUS_COLORS[RecordingStatus.UNKNOWN])


def recording_status_label(status: RecordingStatus) -> str:
    return RECORDING_STATUS_LABELS.get(status, "Unknown")


# KPI summary accents (reuse the status palette so colours stay consistent).
KPI_GOOD = COLORS["online"]
KPI_BAD = COLORS["offline"]
KPI_NEUTRAL = COLORS["text_muted"]


# --------------------------------------------------------------------------- #
# Typography
# --------------------------------------------------------------------------- #
# Monospace family for data (IPs, ports, latency, revisions). IBM Plex Mono is
# the design intent; we fall back to Consolas (ships on Windows) so there is no
# font-install dependency.
FONT_MONO = "Consolas"


# --------------------------------------------------------------------------- #
# Sizing / layout tokens
# --------------------------------------------------------------------------- #
RAIL_WIDTH = 60            # narrow left icon rail
SIDEBAR_WIDTH = 256        # room-list sidebar inside the Rooms view
TOPBAR_HEIGHT = 52         # shared breadcrumb / action bar
CARD_MIN_WIDTH = 300
CARD_HEIGHT = 96
GRID_COLUMNS = 3           # device cards per row in the main grid
PAD = 16                   # standard outer padding
GAP = 12                   # standard gap between widgets
CORNER = 8                 # standard corner radius
CORNER_LG = 12             # larger radius for panels
