"""Central colour palette and sizing tokens for the mission-deck UI.

Keeping these in one place gives the whole app a consistent, enterprise-grade
dark look and makes re-theming a one-file change. Colours are deliberately
low-saturation with a single accent so the status indicators (green/red) pop.
"""

from __future__ import annotations

from mission_deck.models import DeviceStatus, RecordingStatus

# --------------------------------------------------------------------------- #
# Palette (dark)
# --------------------------------------------------------------------------- #
COLORS: dict[str, str] = {
    "bg": "#0f1115",            # app background
    "sidebar": "#15181e",       # left rail
    "panel": "#0f1115",         # main content area
    "header": "#15181e",        # top bars
    "card": "#1b1f27",          # device card fill
    "card_hover": "#222837",    # device card hover
    "border": "#2a2f3a",        # hairline separators / card outlines
    "text": "#e6e8ec",          # primary text
    "text_muted": "#8b929e",    # secondary text
    "text_faint": "#5b616d",    # tertiary / metadata
    "accent": "#3b82f6",        # brand / primary action
    "accent_hover": "#2f6fe0",
    "accent_soft": "#1d2740",   # selected sidebar item fill
    "pill": "#232a36",          # category pill background
    "online": "#22c55e",        # success / command-ok feedback
    "offline": "#ef4444",       # error / command-fail feedback
}

# Status indicator colours, keyed by DeviceStatus.
STATUS_COLORS: dict[DeviceStatus, str] = {
    DeviceStatus.ONLINE: "#22c55e",
    DeviceStatus.OFFLINE: "#ef4444",
    DeviceStatus.CHECKING: "#f59e0b",
    DeviceStatus.UNKNOWN: "#6b7280",
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
    RecordingStatus.RECORDING: "#ef4444",   # red — "on air" convention
    RecordingStatus.PAUSED: "#f59e0b",      # amber
    RecordingStatus.IDLE: "#6b7280",        # gray
    RecordingStatus.UNKNOWN: "#5b616d",     # faint
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


# --------------------------------------------------------------------------- #
# Sizing / layout tokens
# --------------------------------------------------------------------------- #
SIDEBAR_WIDTH = 260
CARD_MIN_WIDTH = 320
CARD_HEIGHT = 96
GRID_COLUMNS = 3            # device cards per row in the main grid
PAD = 16                   # standard outer padding
GAP = 12                   # standard gap between widgets
CORNER = 10                # standard corner radius
