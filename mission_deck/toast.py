"""Non-blocking toast notifications for mission-deck.

Transient, stacked notification cards in the bottom-right corner of the main
window — the modern desktop pattern for "something finished while you were
looking elsewhere" (a sweep completed, devices went offline, an export saved).
They never steal focus, never block, auto-dismiss after a few seconds and can
be clicked away (or clicked through to an action, e.g. jump to the overview).

Pure presentation: :class:`Toaster` owns a small pool of overlay frames placed
over whatever view is active. It performs no I/O and knows nothing about
devices — callers pass finished strings.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from mission_deck.theme import COLORS, CORNER
from mission_deck.ui import font

# Visual vocabulary per kind: the accent strip + glyph colour.
_KIND_COLORS = {
    "info":    COLORS["text_muted"],
    "success": COLORS["online"],
    "error":   COLORS["offline"],
    "warn":    COLORS["warn"],
}

_WIDTH = 360
MAX_TOASTS = 4
DEFAULT_DURATION_MS = 6000


class _Toast(ctk.CTkFrame):
    """One notification card: accent strip, title, optional detail line."""

    def __init__(self, master, toaster: "Toaster", title: str, detail: str,
                 kind: str, on_click: Callable[[], None] | None):
        super().__init__(
            master, width=_WIDTH, corner_radius=CORNER,
            fg_color=COLORS["elev"], border_width=1, border_color=COLORS["border_2"],
        )
        self._toaster = toaster
        self._on_click = on_click
        self._dismiss_job: str | None = None
        accent = _KIND_COLORS.get(kind, _KIND_COLORS["info"])

        self.grid_columnconfigure(1, weight=1)
        # height=10: CTkFrame defaults to 200px, which would inflate the card;
        # sticky="ns" stretches the strip to the real content height anyway.
        strip = ctk.CTkFrame(self, width=3, height=10, corner_radius=2, fg_color=accent)
        strip.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(10, 10), pady=10)

        title_lbl = ctk.CTkLabel(
            self, text=title, anchor="w", justify="left", wraplength=_WIDTH - 90,
            font=font(12, weight="bold"), text_color=COLORS["text"],
        )
        title_lbl.grid(row=0, column=1, sticky="ew", pady=(10, 0 if detail else 10))
        detail_lbl = None
        if detail:
            detail_lbl = ctk.CTkLabel(
                self, text=detail, anchor="w", justify="left", wraplength=_WIDTH - 90,
                font=font(11), text_color=COLORS["text_muted"],
            )
            detail_lbl.grid(row=1, column=1, sticky="ew", pady=(0, 10))

        close = ctk.CTkButton(
            self, text="✕", width=24, height=24, corner_radius=CORNER,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            text_color=COLORS["text_faint"], font=font(11),
            command=self.dismiss,
        )
        close.grid(row=0, column=2, sticky="ne", padx=(6, 8), pady=8)

        # Click anywhere on the body to run the action (if any) and dismiss.
        for widget in (self, strip, title_lbl, *( [detail_lbl] if detail_lbl else [] )):
            widget.bind("<Button-1>", self._clicked)
            widget.configure(cursor="hand2" if on_click else "arrow")

    def _clicked(self, _event=None) -> None:
        action = self._on_click
        self.dismiss()
        if action is not None:
            action()

    def schedule_dismiss(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self._dismiss_job = self.after(duration_ms, self.dismiss)

    def dismiss(self) -> None:
        if self._dismiss_job is not None:
            try:
                self.after_cancel(self._dismiss_job)
            except Exception:
                pass
            self._dismiss_job = None
        self._toaster._remove(self)


class Toaster:
    """Owns the stack of live toasts over ``master`` (the main window)."""

    def __init__(self, master):
        self._master = master
        self._toasts: list[_Toast] = []

    def show(self, title: str, detail: str = "", kind: str = "info",
             duration_ms: int = DEFAULT_DURATION_MS,
             on_click: Callable[[], None] | None = None) -> None:
        """Pop a toast. ``kind``: info | success | warn | error."""

        # The stack is capped: drop the oldest rather than fill the screen.
        while len(self._toasts) >= MAX_TOASTS:
            self._toasts[0].dismiss()
        toast = _Toast(self._master, self, title, detail, kind, on_click)
        self._toasts.append(toast)
        self._layout()
        toast.schedule_dismiss(duration_ms)

    def dismiss_all(self) -> None:
        for toast in list(self._toasts):
            toast.dismiss()

    # ------------------------------------------------------------------ #
    def _remove(self, toast: _Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        try:
            toast.destroy()
        except Exception:
            pass
        self._layout()

    def _layout(self) -> None:
        """Re-place the stack bottom-right, newest at the bottom."""

        y = -16
        for toast in reversed(self._toasts):
            toast.place(relx=1.0, rely=1.0, x=-16, y=y, anchor="se")
            toast.lift()
            toast.update_idletasks()
            y -= toast.winfo_reqheight() + 8
