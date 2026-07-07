"""Live View pop-out window — watch one camera feed inside the app.

Pure presentation over :mod:`mission_deck.stream`. Deliberately **one feed at
a time**: the app owns a single :class:`LiveViewWindow`; opening another
camera swaps the feed in place instead of stacking decoders, so Tk only ever
repaints one video surface and only one ffmpeg process exists.

The window polls the worker's latest-frame slot on the Tk timer
(:data:`RENDER_INTERVAL_MS`), repaints at most one frame per tick, and turns
worker state transitions into toasts + audit events (``stream.open`` /
``stream.drop`` / ``stream.close`` / ``stream.snapshot``). Quality-of-life on
board: a status pill (LIVE / CONNECTING / SIGNAL LOST), live fps/uptime/drop
stats, a same-room camera switcher, PNG snapshots, always-on-top pinning and
a manual reconnect that skips the backoff wait.
"""

from __future__ import annotations

import logging
import time
import tkinter
from collections import deque
from datetime import datetime
from tkinter import filedialog
from typing import TYPE_CHECKING

import customtkinter as ctk
from PIL import Image, ImageTk

from mission_deck.logging_setup import audit
from mission_deck.models import Device, Room
from mission_deck.stream import (
    FRAME_HEIGHT,
    FRAME_RATE,
    FRAME_WIDTH,
    StreamState,
    StreamStats,
    StreamWorker,
    redact_stream_url,
)
from mission_deck.theme import COLORS, CORNER, GAP, PAD
from mission_deck.ui import BTN_GHOST, BTN_OUTLINE, font, style

if TYPE_CHECKING:  # avoid a circular import — app.py imports this module
    from mission_deck.app import App

logger = logging.getLogger(__name__)

# How often the window pulls the newest frame from the worker (~15 fps).
RENDER_INTERVAL_MS = 66

_PILL_STYLES: dict[str, tuple[str, str]] = {
    # key → (text colour, pill text). Keys are derived, not raw StreamState.
    "live":       (COLORS["online"], "● LIVE"),
    "connecting": (COLORS["warn"], "◌ CONNECTING…"),
    "lost":       (COLORS["offline"], "● SIGNAL LOST"),
    "stopped":    (COLORS["unknown"], "○ STOPPED"),
    "failed":     (COLORS["offline"], "✕ UNAVAILABLE"),
}


def streamable_devices(room: Room) -> list[Device]:
    """Devices in ``room`` with a configured stream, in device order."""

    return [d for d in room.devices if d.stream_url]


class LiveViewWindow(ctk.CTkToplevel):
    """The single live-video pop-out. Create via :meth:`App.open_live_view`.

    One instance app-wide; :meth:`show_device` swaps the feed in place so only
    one ffmpeg process and one repainting video surface ever exist. The window
    is an independent (non-modal, non-transient) Toplevel so operators can
    park it on a second monitor; PIN toggles always-on-top.
    """

    def __init__(self, app: App, room: Room, device: Device):
        super().__init__(app)
        self.app = app
        self.room: Room = room
        self.device: Device = device
        self._worker: StreamWorker | None = None
        self._closed = False
        self._pinned = False

        # Render bookkeeping.
        self._photo: ImageTk.PhotoImage | None = None
        self._photo_size: tuple[int, int] = (0, 0)
        self._last_seq = -1
        self._last_frame: bytes | None = None
        self._fps_window: deque[tuple[float, int]] = deque(maxlen=32)
        self._seen_drops = 0
        self._was_live = False
        self._stats_throttle = 0
        self._opened_at = time.monotonic()

        self.configure(fg_color=COLORS["bg"])
        self.geometry("1000x660")
        self.minsize(560, 420)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # --- Header: status pill · camera name · source chip ---------------- #
        head = ctk.CTkFrame(self, fg_color=COLORS["rail"], corner_radius=0)
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(2, weight=1)
        self._pill = ctk.CTkLabel(
            head, text="", width=130, anchor="w",
            font=font(12, mono=True, weight="bold"),
        )
        self._pill.grid(row=0, column=0, padx=(PAD, GAP), pady=10)
        self._title = ctk.CTkLabel(
            head, text="", anchor="w",
            font=font(14, weight="bold"), text_color=COLORS["text"],
        )
        self._title.grid(row=0, column=1, sticky="w")
        self._source = ctk.CTkLabel(
            head, text="", anchor="e",
            font=font(10, mono=True), text_color=COLORS["text_faint"],
        )
        self._source.grid(row=0, column=2, sticky="e", padx=(GAP, PAD))

        # --- Video surface --------------------------------------------------- #
        # A plain tk.Canvas: CTk widgets add scaling/round-rect layers we do
        # not want under a 15 fps repaint. PhotoImage.paste() updates in place.
        self._canvas = tkinter.Canvas(
            self, bg="#000000", highlightthickness=0, bd=0,
        )
        self._canvas.grid(row=1, column=0, sticky="nsew")
        self._image_item = self._canvas.create_image(0, 0, anchor="nw")

        # Centre overlay for connecting / lost / failed states.
        self._overlay = ctk.CTkFrame(
            self._canvas, fg_color=COLORS["panel"], corner_radius=CORNER,
            border_width=1, border_color=COLORS["border_2"],
        )
        self._overlay_title = ctk.CTkLabel(
            self._overlay, text="", font=font(14, mono=True, weight="bold"),
        )
        self._overlay_title.pack(padx=24, pady=(18, 4))
        self._overlay_detail = ctk.CTkLabel(
            self._overlay, text="", font=font(11), text_color=COLORS["text_muted"],
            wraplength=420, justify="center",
        )
        self._overlay_detail.pack(padx=24, pady=(0, 18))
        self._overlay_visible = False

        # --- Footer: stats · camera switcher · actions ----------------------- #
        foot = ctk.CTkFrame(self, fg_color=COLORS["rail"], corner_radius=0)
        foot.grid(row=2, column=0, sticky="ew")
        foot.grid_columnconfigure(0, weight=1)
        self._stats = ctk.CTkLabel(
            foot, text="", anchor="w",
            font=font(10, mono=True), text_color=COLORS["text_muted"],
        )
        self._stats.grid(row=0, column=0, sticky="ew", padx=(PAD, GAP), pady=10)

        self._switcher_var = ctk.StringVar()
        self._switcher = ctk.CTkOptionMenu(
            foot, variable=self._switcher_var, width=210, height=30,
            corner_radius=CORNER, dynamic_resizing=False,
            fg_color=COLORS["card"], button_color=COLORS["card_2"],
            button_hover_color=COLORS["card_hover"],
            dropdown_fg_color=COLORS["elev"], dropdown_hover_color=COLORS["card_hover"],
            text_color=COLORS["text"], dropdown_text_color=COLORS["text"],
            font=font(11, mono=True), dropdown_font=font(11, mono=True),
            command=self._on_switch_camera,
        )
        self._switch_map: dict[str, Device] = {}

        self._pin_btn = ctk.CTkButton(
            foot, text="PIN", width=64, height=30, command=self._toggle_pin,
            font=font(10, mono=True), **BTN_GHOST,
        )
        self._pin_btn.grid(row=0, column=2, padx=(0, GAP))
        ctk.CTkButton(
            foot, text="SNAPSHOT", width=96, height=30, command=self._snapshot,
            font=font(10, mono=True), **BTN_OUTLINE,
        ).grid(row=0, column=3, padx=(0, GAP))
        ctk.CTkButton(
            foot, text="RECONNECT", width=104, height=30, command=self._reconnect,
            font=font(10, mono=True),
            **style(BTN_OUTLINE, border_color=COLORS["accent2_line"],
                    text_color=COLORS["accent2_text"]),
        ).grid(row=0, column=4, padx=(0, PAD))

        self.protocol("WM_DELETE_WINDOW", self.close)
        # Belt and braces: a cascaded Tk teardown (root destroyed) bypasses
        # close(); the <Destroy> binding still stops ffmpeg.
        self.bind("<Destroy>", self._on_tk_destroy)

        self._start_stream(room, device)
        self.after(RENDER_INTERVAL_MS, self._tick)

    # ------------------------------------------------------------------ #
    # Feed lifecycle
    # ------------------------------------------------------------------ #
    def show_device(self, room: Room, device: Device) -> None:
        """Swap the window to another camera's feed (the singleton contract)."""

        if device is self.device and self._worker is not None:
            return
        self._audit_close("switched")
        self._stop_worker()
        self._start_stream(room, device)

    def _start_stream(self, room: Room, device: Device) -> None:
        self.room = room
        self.device = device
        url = device.stream_url or ""
        self.title(f"Live View — {device.name}")
        self._title.configure(text=device.name)
        self._source.configure(text=f"{room.name} · {redact_stream_url(url)}")
        self._refresh_switcher()

        self._last_seq = -1
        self._last_frame = None
        self._fps_window.clear()
        self._seen_drops = 0
        self._was_live = False
        self._opened_at = time.monotonic()
        self._canvas.itemconfigure(self._image_item, image="")
        self._photo = None
        self._photo_size = (0, 0)

        self._worker = StreamWorker(url)
        self._worker.start()
        audit(
            "stream.open",
            device_id=device.id,
            device_name=device.name,
            room=room.name,
            url=redact_stream_url(url),
        )

    def _stop_worker(self) -> None:
        if self._worker is not None:
            self._worker.stop()
            self._worker = None

    def _audit_close(self, reason: str) -> None:
        if self._worker is None:
            return
        stats = self._worker.stats()
        audit(
            "stream.close",
            device_id=self.device.id,
            device_name=self.device.name,
            reason=reason,
            frames=stats.frames,
            drops=stats.drops,
            duration_seconds=round(time.monotonic() - self._opened_at, 1),
        )

    def close(self) -> None:
        """User closed the window: stop ffmpeg, audit, release the singleton."""

        if self._closed:
            return
        self._closed = True
        self._audit_close("closed")
        self._stop_worker()
        if getattr(self.app, "_live_view", None) is self:
            self.app._live_view = None
        self.destroy()

    def _on_tk_destroy(self, event) -> None:
        if event.widget is self and not self._closed:
            self._closed = True
            self._stop_worker()
            if getattr(self.app, "_live_view", None) is self:
                self.app._live_view = None

    # ------------------------------------------------------------------ #
    # Render loop (Tk thread)
    # ------------------------------------------------------------------ #
    def _tick(self) -> None:
        if self._closed:
            return
        worker = self._worker
        if worker is not None:
            latest = worker.latest_frame()
            if latest is not None and latest[0] != self._last_seq:
                self._last_seq, self._last_frame = latest
                self._fps_window.append((time.monotonic(), latest[0]))
                self._render(self._last_frame)
            self._apply_state(worker.stats())
        self.after(RENDER_INTERVAL_MS, self._tick)

    def _render(self, frame: bytes) -> None:
        """Paint one rgb24 frame, letterbox-fitted to the current canvas size."""

        cw = max(self._canvas.winfo_width(), 1)
        ch = max(self._canvas.winfo_height(), 1)
        scale = min(cw / FRAME_WIDTH, ch / FRAME_HEIGHT)
        tw = max(int(FRAME_WIDTH * scale), 1)
        th = max(int(FRAME_HEIGHT * scale), 1)

        image = Image.frombuffer("RGB", (FRAME_WIDTH, FRAME_HEIGHT), frame, "raw", "RGB", 0, 1)
        if (tw, th) != (FRAME_WIDTH, FRAME_HEIGHT):
            image = image.resize((tw, th), Image.Resampling.BILINEAR)

        if self._photo is None or self._photo_size != (tw, th):
            self._photo = ImageTk.PhotoImage(image)
            self._photo_size = (tw, th)
            self._canvas.itemconfigure(self._image_item, image=self._photo)
        else:
            self._photo.paste(image)
        self._canvas.coords(self._image_item, (cw - tw) // 2, (ch - th) // 2)

    # ------------------------------------------------------------------ #
    # State presentation
    # ------------------------------------------------------------------ #
    def _apply_state(self, stats: StreamStats) -> None:
        # Detect a drop transition (the worker counts them; the UI reports).
        if stats.drops > self._seen_drops:
            self._seen_drops = stats.drops
            audit(
                "stream.drop",
                device_id=self.device.id,
                device_name=self.device.name,
                drops=stats.drops,
                error=stats.last_error,
            )
            self.app.toaster.show(
                "Live view signal lost",
                f"{self.device.name} — reconnecting…",
                kind="warn",
            )
        if stats.state is StreamState.LIVE and not self._was_live:
            self._was_live = True
            if stats.drops > 0:
                self.app.toaster.show(
                    "Live view restored", self.device.name, kind="success",
                )
        elif stats.state is not StreamState.LIVE:
            self._was_live = False

        key = self._pill_key(stats)
        color, text = _PILL_STYLES[key]
        self._pill.configure(text=text, text_color=color)
        self._update_overlay(key, stats)

        self._stats_throttle += 1
        if self._stats_throttle % 8 == 0:  # ~every 500 ms
            self._stats.configure(text=self._stats_text(stats))

    @staticmethod
    def _pill_key(stats: StreamStats) -> str:
        if stats.state is StreamState.LIVE:
            return "live"
        if stats.state is StreamState.STOPPED:
            return "stopped"
        if stats.state is StreamState.FAILED:
            return "failed"
        # CONNECTING: a reconnect after a drop reads as an alert, a first
        # connect as routine.
        return "lost" if stats.drops > 0 else "connecting"

    def _update_overlay(self, key: str, stats: StreamStats) -> None:
        if key == "live":
            if self._overlay_visible:
                self._overlay.place_forget()
                self._overlay_visible = False
            return
        titles = {
            "connecting": ("CONNECTING…", COLORS["warn"]),
            "lost": (f"SIGNAL LOST — RECONNECTING (ATTEMPT {stats.attempts})",
                     COLORS["offline"]),
            "stopped": ("STOPPED", COLORS["text_muted"]),
            "failed": ("STREAM UNAVAILABLE", COLORS["offline"]),
        }
        title, color = titles[key]
        detail = stats.last_error
        if key == "failed" and "ffmpeg" in detail:
            detail += " — install ffmpeg or set MISSION_DECK_FFMPEG"
        self._overlay_title.configure(text=title, text_color=color)
        self._overlay_detail.configure(text=detail)
        if not self._overlay_visible:
            self._overlay.place(relx=0.5, rely=0.5, anchor="center")
            self._overlay_visible = True

    def _stats_text(self, stats: StreamStats) -> str:
        fps = 0.0
        if len(self._fps_window) >= 2:
            (t0, s0), (t1, s1) = self._fps_window[0], self._fps_window[-1]
            if t1 > t0:
                fps = (s1 - s0) / (t1 - t0)
        if stats.connected_since is not None:
            up = int(time.monotonic() - stats.connected_since)
            uptime = f"{up // 3600:02d}:{up % 3600 // 60:02d}:{up % 60:02d}"
        else:
            uptime = "—"
        return (
            f"{fps:4.1f} FPS · FRAMES {stats.frames} · DROPS {stats.drops}"
            f" · UP {uptime} · {FRAME_WIDTH}×{FRAME_HEIGHT}@{FRAME_RATE}"
        )

    # ------------------------------------------------------------------ #
    # Footer actions
    # ------------------------------------------------------------------ #
    def _refresh_switcher(self) -> None:
        """Offer the room's other streamable cameras — switching swaps the feed."""

        cameras = streamable_devices(self.room)
        self._switch_map = {}
        values: list[str] = []
        for cam in cameras:
            label = cam.name if cam.name not in self._switch_map else f"{cam.name} ({cam.id})"
            self._switch_map[label] = cam
            values.append(label)
        if len(values) > 1:
            self._switcher.configure(values=values)
            current = next((lbl for lbl, d in self._switch_map.items() if d is self.device), "")
            self._switcher_var.set(current)
            self._switcher.grid(row=0, column=1, padx=(0, GAP))
        else:
            self._switcher.grid_forget()

    def _on_switch_camera(self, label: str) -> None:
        device = self._switch_map.get(label)
        if device is not None and device is not self.device:
            self.show_device(self.room, device)

    def _reconnect(self) -> None:
        if self._worker is not None:
            self._worker.retry_now()

    def _toggle_pin(self) -> None:
        self._pinned = not self._pinned
        try:
            self.attributes("-topmost", self._pinned)
        except Exception:  # unsupported on this WM — cosmetic
            logger.debug("Could not toggle -topmost", exc_info=True)
            self._pinned = False
        self._pin_btn.configure(text="UNPIN" if self._pinned else "PIN")

    def _snapshot(self) -> None:
        """Save the newest frame as a PNG — timestamped evidence of the feed."""

        frame = self._last_frame
        if frame is None:
            self.app.toaster.show("No frame to save yet", kind="warn")
            return
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save snapshot as…",
            defaultextension=".png",
            initialfile=f"{self.device.id}-{stamp}.png",
            filetypes=[("PNG image", "*.png"), ("All files", "*.*")],
        )
        if not path:
            return
        device = self.device

        def work() -> str:
            image = Image.frombuffer(
                "RGB", (FRAME_WIDTH, FRAME_HEIGHT), frame, "raw", "RGB", 0, 1,
            )
            image.save(path, format="PNG")
            return path

        def on_done(ok: bool, message: str) -> None:
            audit(
                "stream.snapshot",
                device_id=device.id,
                device_name=device.name,
                path=path,
                ok=ok,
            )
            if ok:
                self.app.toaster.show("Snapshot saved", message, kind="success")
            else:
                self.app.toaster.show("Snapshot failed", message, kind="error")

        self.app.run_background(work, on_done)
