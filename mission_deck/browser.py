"""Open device web UIs in the user's browser.

This re-creates the headline behaviour of the legacy PowerShell tool: one click
opens every web-accessible device in a room, popping out in the browser.

Browser selection (from the config ``app.browser`` block):

  * ``path``  – an explicit browser executable. This is the most reliable
                option and the only way to *guarantee* a fresh window: for
                Chromium-family browsers (Chrome/Edge/Brave/…) we pass
                ``--new-window`` so all URLs open as tabs in one new window.
  * ``name``  – a :mod:`webbrowser` registered name (e.g. ``"firefox"``).
  * (neither) – the operating-system default browser is used.

``new_window`` (default ``True``) requests a new window rather than reusing an
existing one. Without an explicit ``path`` this is only a hint, since the OS
default-browser handler decides window-vs-tab.

Opening is done on a background thread so launching a dozen tabs never blocks
the Tk UI thread.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import webbrowser
from collections.abc import Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Executable name fragments that accept Chromium's ``--new-window`` flag.
_CHROMIUM_HINTS = ("chrome", "chromium", "edge", "msedge", "brave", "vivaldi", "opera")

# Friendly browser names we can try to resolve to an executable so that
# ``--new-window`` works even when only a name (not a path) is configured.
_NAME_TO_EXECUTABLES: dict[str, tuple[str, ...]] = {
    "chrome": ("chrome", "google-chrome", "google-chrome-stable"),
    "google-chrome": ("chrome", "google-chrome", "google-chrome-stable"),
    "edge": ("msedge", "microsoft-edge"),
    "msedge": ("msedge", "microsoft-edge"),
    "brave": ("brave", "brave-browser"),
    "chromium": ("chromium", "chromium-browser"),
    "vivaldi": ("vivaldi",),
    "opera": ("opera",),
}


@dataclass(slots=True)
class BrowserConfig:
    """Resolved browser preferences from the app config."""

    name: str | None = None
    path: str | None = None
    new_window: bool = True

    @classmethod
    def from_settings(cls, settings: dict) -> BrowserConfig:
        """Build from the ``app`` settings block. Accepts a string or object.

        ``"browser": "firefox"``            -> by name
        ``"browser": "C:/.../chrome.exe"``  -> by path
        ``"browser": {"path": ..., "name": ..., "new_window": true}``
        """

        raw = settings.get("browser")
        if not raw:
            return cls()
        if isinstance(raw, str):
            if ("/" in raw) or ("\\" in raw) or raw.lower().endswith(".exe"):
                return cls(path=raw)
            return cls(name=raw)
        if isinstance(raw, dict):
            return cls(
                name=(raw.get("name") or None),
                path=(raw.get("path") or None),
                new_window=bool(raw.get("new_window", True)),
            )
        return cls()


def _looks_chromium(executable: str) -> bool:
    low = executable.lower()
    return any(hint in low for hint in _CHROMIUM_HINTS)


def _resolve_named_executable(name: str | None) -> str | None:
    """Best-effort resolve a friendly name to an executable on PATH."""

    if not name:
        return None
    for candidate in _NAME_TO_EXECUTABLES.get(name.lower(), (name,)):
        found = shutil.which(candidate)
        if found:
            return found
    return None


def open_urls(urls: Iterable[str], cfg: BrowserConfig, *, in_thread: bool = True) -> int:
    """Open every URL in ``urls`` in the configured browser.

    Returns the number of URLs that will be opened (0 if none). The actual
    launch happens on a daemon thread by default so the caller (the Tk UI
    thread) never blocks.
    """

    url_list = [u for u in urls if u]
    if not url_list:
        return 0

    if in_thread:
        threading.Thread(
            target=_open_blocking, args=(url_list, cfg), daemon=True
        ).start()
    else:
        _open_blocking(url_list, cfg)
    return len(url_list)


def _open_blocking(urls: list[str], cfg: BrowserConfig) -> None:
    # 1) Explicit/resolved executable -> subprocess gives us real new-window
    #    control and opens all URLs in a single window for Chromium browsers.
    executable = cfg.path or _resolve_named_executable(cfg.name)
    if executable:
        args = [executable]
        if cfg.new_window and _looks_chromium(executable):
            args.append("--new-window")
        args.extend(urls)
        try:
            subprocess.Popen(args, close_fds=True)
            logger.info("Opened %d URL(s) via %s", len(urls), executable)
            return
        except OSError as exc:
            logger.warning(
                "Could not launch browser '%s' (%s); falling back to default", executable, exc
            )

    # 2) Fall back to the webbrowser module (OS default or a registered name).
    try:
        browser = webbrowser.get(cfg.name) if cfg.name else webbrowser.get()
    except webbrowser.Error as exc:
        logger.warning(
            "Browser '%s' not registered (%s); using OS default", cfg.name, exc
        )
        browser = webbrowser.get()

    for index, url in enumerate(urls):
        # First URL hints "new window", the rest open as tabs alongside it.
        new = 1 if (index == 0 and cfg.new_window) else 2
        browser.open(url, new=new, autoraise=(index == 0))
    logger.info("Opened %d URL(s) via webbrowser module", len(urls))
