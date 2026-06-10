"""Shared widget styling for mission-deck: cached fonts + button style tokens.

Tk font objects are expensive and the UI used to build one per label — at
100+ device cards and 200-row dashboard lists that is thousands of identical
font objects. :func:`font` returns a process-wide cached ``CTkFont`` instead,
so every "12px mono" label shares one object.

The ``BTN_*`` dicts are the Nothing-style button vocabulary (solid = primary,
outline = secondary, danger = destructive). Use :func:`style` to override a
token without mutating the shared dict.
"""

from __future__ import annotations

import tkinter

import customtkinter as ctk

from mission_deck.theme import COLORS, FONT_MONO, FONT_UI

_FONT_CACHE: dict[tuple[str, int, str], ctk.CTkFont] = {}
_CACHE_ROOT: tkinter.Misc | None = None


def font(size: int, *, mono: bool = False, weight: str = "normal") -> ctk.CTkFont:
    """A shared, cached font. ``mono`` selects the data/label family.

    Fonts belong to a Tk interpreter, so the cache is invalidated whenever the
    default root changes (welcome window → main app, config-switch restart).
    """

    global _CACHE_ROOT
    root = tkinter._default_root
    if root is not _CACHE_ROOT:
        _FONT_CACHE.clear()
        _CACHE_ROOT = root
    family = FONT_MONO if mono else FONT_UI
    key = (family, size, weight)
    cached = _FONT_CACHE.get(key)
    if cached is None:
        cached = _FONT_CACHE[key] = ctk.CTkFont(family=family, size=size, weight=weight)
    return cached


def style(base: dict, **overrides) -> dict:
    """Merge style ``overrides`` onto a shared token dict (non-mutating)."""

    return {**base, **overrides}


# Primary action: white fill, black text. One per screen.
BTN_SOLID: dict = {
    "corner_radius": 999,
    "fg_color": COLORS["text"],
    "hover_color": COLORS["text_muted"],
    "text_color": COLORS["bg"],
}

# Secondary action: hairline outline, transparent fill.
BTN_OUTLINE: dict = {
    "corner_radius": 999,
    "fg_color": "transparent",
    "hover_color": COLORS["card_hover"],
    "border_width": 1,
    "border_color": COLORS["border_2"],
    "text_color": COLORS["text"],
}

# Quiet secondary: same shape, recedes into the chrome.
BTN_GHOST: dict = style(BTN_OUTLINE, border_color=COLORS["border"], text_color=COLORS["text_muted"])

# Destructive: red is an interrupt, never a default.
BTN_DANGER: dict = style(BTN_OUTLINE, border_color=COLORS["offline"], text_color=COLORS["offline"])

# Switches: kill the CTk default blue; accent track when on, white knob.
SWITCH: dict = {
    "progress_color": COLORS["accent"],
    "button_color": "#ffffff",
    "button_hover_color": "#dddddd",
    "fg_color": COLORS["border_2"],
}
