"""Live camera stream decoding (RTSP/RTMP via ffmpeg) — no GUI.

A camera opts in with a single ``stream_url`` config key (``rtsp://…`` or
``rtmp://…``). This module turns that URL into a steady supply of raw RGB
frames: an external **ffmpeg** process decodes the feed onto a pipe — no
third-party Python video stack — and :class:`StreamWorker` publishes the
newest frame into a lock-protected slot for the UI to pull.

Like ``network.py``, this module never touches Tk. The presentation half (the
pop-out window) lives in :mod:`mission_deck.live_view` and polls
:meth:`StreamWorker.latest_frame` / :meth:`StreamWorker.stats` on the Tk
timer.

Stream-drop detection is owned here: a watchdog thread judges liveness by
*frame arrival* (ffmpeg happily blocks forever on a camera that stopped
sending), kills a silent process to unblock the pipe read, and the run loop
reconnects with capped exponential backoff until :meth:`StreamWorker.stop`.

ffmpeg is located via the ``MISSION_DECK_FFMPEG`` env var, next to the frozen
executable, or on ``PATH`` — see :func:`find_ffmpeg`.
"""

from __future__ import annotations

import enum
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Tuning constants
# --------------------------------------------------------------------------- #
# ffmpeg letterboxes every source into this fixed geometry, so each raw frame
# on the pipe is exactly FRAME_BYTES and the Tk-side cost is predictable no
# matter what the camera sends.
FRAME_WIDTH = 960
FRAME_HEIGHT = 540
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3  # rgb24

# Decode frame-rate cap. Courtroom monitoring does not need 60 fps, and this
# bounds both pipe throughput and Tk repaint work.
FRAME_RATE = 15

# Watchdog thresholds: a live feed with no frame for STALL_SECONDS is dropped;
# a (re)connect that produces no frame within CONNECT_TIMEOUT_SECONDS is
# abandoned and retried.
STALL_SECONDS = 5.0
CONNECT_TIMEOUT_SECONDS = 12.0

# Reconnect backoff after a drop/failed connect; stays at the last value.
RECONNECT_BACKOFF = (1.0, 2.0, 4.0, 8.0, 15.0)

_USERINFO_RE = re.compile(r"^([a-z][a-z0-9+.-]*://)[^/@]*@", re.IGNORECASE)


def redact_stream_url(url: str) -> str:
    """Strip ``user:password@`` credentials from a stream URL for display/logs."""

    return _USERINFO_RE.sub(r"\1", url)


# --------------------------------------------------------------------------- #
# ffmpeg discovery / command line
# --------------------------------------------------------------------------- #
def find_ffmpeg() -> str | None:
    """Locate the ffmpeg binary, or ``None`` if it isn't installed.

    Order: the ``MISSION_DECK_FFMPEG`` env var, an ``ffmpeg(.exe)`` sitting
    next to the (frozen) executable, then ``PATH``.
    """

    override = os.environ.get("MISSION_DECK_FFMPEG", "").strip()
    if override:
        if Path(override).is_file():
            return override
        logger.warning("MISSION_DECK_FFMPEG=%r does not exist; ignoring", override)

    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if getattr(sys, "frozen", False):
        bundled = Path(sys.executable).parent / exe_name
        if bundled.is_file():
            return str(bundled)

    return shutil.which("ffmpeg")


def build_ffmpeg_command(ffmpeg: str, url: str) -> list[str]:
    """The ffmpeg invocation that decodes ``url`` to raw rgb24 on stdout.

    The output is capped at :data:`FRAME_RATE` fps and letterboxed into a fixed
    ``FRAME_WIDTH×FRAME_HEIGHT`` box so the reader can consume exact
    :data:`FRAME_BYTES` chunks. RTSP is forced onto TCP: interleaved transport
    survives the firewalled court networks where UDP RTP does not, and avoids
    packet-loss smearing. Liveness is *not* delegated to ffmpeg timeout flags
    (they vary across builds) — the worker's watchdog owns that.
    """

    cmd = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error", "-nostdin",
        "-fflags", "nobuffer", "-flags", "low_delay",
    ]
    if url.lower().startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp"]
    cmd += [
        "-i", url,
        "-an", "-sn", "-dn",
        "-vf",
        (
            f"fps={FRAME_RATE},"
            f"scale={FRAME_WIDTH}:{FRAME_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={FRAME_WIDTH}:{FRAME_HEIGHT}:(ow-iw)/2:(oh-ih)/2"
        ),
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    return cmd


# --------------------------------------------------------------------------- #
# Stream worker
# --------------------------------------------------------------------------- #
class StreamState(enum.Enum):
    """Lifecycle of one stream worker, as shown by the window's status pill."""

    CONNECTING = "connecting"  # spawning ffmpeg / waiting for the first frame
    LIVE = "live"              # frames are flowing
    STOPPED = "stopped"        # stop() was called
    FAILED = "failed"          # unrecoverable (ffmpeg missing / not spawnable)


@dataclass(slots=True)
class StreamStats:
    """A point-in-time snapshot of worker state, safe to read on the Tk thread."""

    state: StreamState
    frames: int            # total frames decoded across all connects
    drops: int             # times a live feed stalled/ended unexpectedly
    attempts: int          # connection attempts so far (1 = first connect)
    connected_since: float | None  # time.monotonic() of going live, else None
    last_error: str        # newest ffmpeg stderr line / watchdog reason


class StreamWorker:
    """Owns one ffmpeg process and publishes its newest frame thread-safely.

    ``start()`` spawns two daemon threads: the run loop (spawn ffmpeg → read
    frames → on loss, back off and reconnect forever until ``stop()``) and a
    watchdog that kills a silent process so the blocking pipe read always
    unblocks. The UI reads :meth:`latest_frame` / :meth:`stats` and never
    blocks on the pipe.
    """

    def __init__(
        self,
        url: str,
        *,
        ffmpeg: str | None = None,
        stall_seconds: float = STALL_SECONDS,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        backoff: tuple[float, ...] = RECONNECT_BACKOFF,
    ) -> None:
        self.url = url
        self._ffmpeg = ffmpeg if ffmpeg is not None else find_ffmpeg()
        self._stall_seconds = stall_seconds
        self._connect_timeout = connect_timeout
        self._backoff = backoff or (1.0,)

        self._lock = threading.Lock()
        self._frame: bytes | None = None
        self._seq = 0
        self._state = StreamState.CONNECTING
        self._frames = 0
        self._drops = 0
        self._attempts = 0
        self._connected_since: float | None = None
        self._last_error = ""

        self._proc: subprocess.Popen | None = None
        self._last_activity = time.monotonic()
        self._stop = threading.Event()
        self._retry_now = threading.Event()

    # ------------------------------------------------------------------ #
    # Public API (any thread)
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="stream-decode").start()
        threading.Thread(target=self._watchdog, daemon=True, name="stream-watchdog").start()

    def stop(self) -> None:
        """Stop decoding and kill ffmpeg. Idempotent; never blocks the caller."""

        self._stop.set()
        self._retry_now.set()  # unblock a backoff wait immediately
        self._kill_proc()
        with self._lock:
            if self._state is not StreamState.FAILED:
                self._state = StreamState.STOPPED
            self._connected_since = None

    def retry_now(self) -> None:
        """Manual reconnect: skip any backoff wait and recycle the process."""

        self._retry_now.set()
        self._kill_proc()

    def latest_frame(self) -> tuple[int, bytes] | None:
        """``(sequence, rgb24 bytes)`` of the newest decoded frame, if any."""

        with self._lock:
            if self._frame is None:
                return None
            return (self._seq, self._frame)

    def stats(self) -> StreamStats:
        with self._lock:
            return StreamStats(
                state=self._state,
                frames=self._frames,
                drops=self._drops,
                attempts=self._attempts,
                connected_since=self._connected_since,
                last_error=self._last_error,
            )

    # ------------------------------------------------------------------ #
    # Run loop (worker thread)
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        if not self._ffmpeg:
            with self._lock:
                self._state = StreamState.FAILED
                self._last_error = "ffmpeg not found"
            return

        backoff_index = 0
        while not self._stop.is_set():
            with self._lock:
                self._attempts += 1
                self._state = StreamState.CONNECTING
                self._connected_since = None
            self._last_activity = time.monotonic()

            try:
                proc = self._spawn()
            except OSError as exc:
                logger.error("Could not start ffmpeg (%s): %s", self._ffmpeg, exc)
                with self._lock:
                    self._state = StreamState.FAILED
                    self._last_error = f"could not start ffmpeg: {exc}"
                return
            self._proc = proc
            threading.Thread(
                target=self._drain_stderr, args=(proc,), daemon=True, name="stream-stderr",
            ).start()

            got_frame = self._read_frames(proc)
            self._proc = None
            self._kill(proc)

            if self._stop.is_set():
                break
            if got_frame:
                # We were live and lost the feed — that is a drop.
                with self._lock:
                    self._drops += 1
                backoff_index = 0  # the camera was reachable; retry briskly
                logger.warning("Stream dropped (%s): %s",
                               redact_stream_url(self.url), self._last_error or "stream ended")
            delay = self._backoff[min(backoff_index, len(self._backoff) - 1)]
            backoff_index += 1
            self._retry_now.wait(delay)
            self._retry_now.clear()

        with self._lock:
            if self._state is not StreamState.FAILED:
                self._state = StreamState.STOPPED
            self._connected_since = None

    def _spawn(self) -> subprocess.Popen:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        return subprocess.Popen(
            build_ffmpeg_command(self._ffmpeg or "ffmpeg", self.url),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=creationflags,
        )

    def _read_frames(self, proc: subprocess.Popen) -> bool:
        """Consume frames until EOF/stop. Returns True if any frame arrived."""

        stdout = proc.stdout
        assert stdout is not None
        got_frame = False
        while not self._stop.is_set():
            chunk = self._read_exact(stdout, FRAME_BYTES)
            if chunk is None:
                break
            self._last_activity = time.monotonic()
            with self._lock:
                self._frame = chunk
                self._seq += 1
                self._frames += 1
                if not got_frame:
                    self._state = StreamState.LIVE
                    self._connected_since = time.monotonic()
                    self._last_error = ""
            got_frame = True
        return got_frame

    @staticmethod
    def _read_exact(stream, count: int) -> bytes | None:
        """Read exactly ``count`` bytes, or ``None`` on EOF (process gone)."""

        parts: list[bytes] = []
        remaining = count
        while remaining > 0:
            chunk = stream.read(remaining)
            if not chunk:
                return None
            parts.append(chunk)
            remaining -= len(chunk)
        return b"".join(parts)

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        """Keep the newest ffmpeg error line so the UI can show *why* it died."""

        stderr = proc.stderr
        if stderr is None:
            return
        try:
            for raw in iter(stderr.readline, b""):
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    logger.debug("ffmpeg: %s", line)
                    with self._lock:
                        self._last_error = line
        except Exception:  # reading a dying pipe — never let this escape
            pass

    # ------------------------------------------------------------------ #
    # Watchdog (worker thread)
    # ------------------------------------------------------------------ #
    def _watchdog(self) -> None:
        """Kill ffmpeg when the feed goes silent, unblocking the pipe read.

        This is the stream-drop detector: ffmpeg happily blocks forever on a
        camera that stopped sending (half-open TCP, paused encoder), so
        liveness is judged by frame arrival, not process health.
        """

        while not self._stop.wait(0.5):
            proc = self._proc
            if proc is None or proc.poll() is not None:
                continue
            with self._lock:
                live = self._state is StreamState.LIVE
            limit = self._stall_seconds if live else self._connect_timeout
            if time.monotonic() - self._last_activity > limit:
                reason = (
                    f"no frames for {limit:.0f}s — feed stalled" if live
                    else f"no video within {limit:.0f}s — connect timed out"
                )
                with self._lock:
                    self._last_error = reason
                logger.warning("Stream watchdog (%s): %s", redact_stream_url(self.url), reason)
                self._kill(proc)

    # ------------------------------------------------------------------ #
    def _kill_proc(self) -> None:
        proc = self._proc
        if proc is not None:
            self._kill(proc)

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=2)
        except Exception:  # already gone / not started — nothing to clean up
            pass
