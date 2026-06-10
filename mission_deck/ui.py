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


class PromptDialog(ctk.CTkToplevel):
    """A Nothing-styled modal text prompt (drop-in for ``CTkInputDialog``).

    ``get_input()`` blocks until the dialog closes and returns the entered
    string, or ``None`` on cancel/close — same contract as CTkInputDialog.
    """

    def __init__(self, parent, title: str, prompt: str):
        super().__init__(parent)
        self._value: str | None = None
        self.title(title)
        self.configure(fg_color=COLORS["bg"])
        self.resizable(False, False)
        self.transient(parent)
        self.geometry(f"+{parent.winfo_rootx() + 60}+{parent.winfo_rooty() + 60}")
        self.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self, text=prompt, anchor="w", justify="left", wraplength=380,
            font=font(12), text_color=COLORS["text_muted"],
        ).grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))
        self._entry = ctk.CTkEntry(
            self, width=400, height=34, corner_radius=8,
            border_width=1, border_color=COLORS["border_2"],
            fg_color=COLORS["card"], text_color=COLORS["text"],
            font=font(12, mono=True),
        )
        self._entry.grid(row=1, column=0, sticky="ew", padx=20)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="e", padx=20, pady=(14, 18))
        ctk.CTkButton(
            bar, text="CANCEL", width=92, height=32,
            font=font(11, mono=True), command=self._cancel, **BTN_GHOST,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkButton(
            bar, text="OK", width=92, height=32,
            font=font(11, mono=True, weight="bold"), command=self._ok, **BTN_SOLID,
        ).pack(side="left")

        self.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(60, self._focus)

    def _focus(self) -> None:
        # Focus/grab can fail transiently on some window managers; cosmetic.
        try:
            self.lift()
            self.grab_set()
            self._entry.focus_set()
        except Exception:
            pass

    def _ok(self) -> None:
        self._value = self._entry.get()
        self.destroy()

    def _cancel(self) -> None:
        self._value = None
        self.destroy()

    def get_input(self) -> str | None:
        self.master.wait_window(self)
        return self._value
