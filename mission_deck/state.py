"""Persisted user state and GUI-managed preferences.

This is what lets a non-technical user *never* touch JSON:

  * the last config they opened is remembered, so the app re-opens it on launch
    without prompting;
  * preferences they set in the Settings dialog (ping timeout, auto-refresh,
    browser, appearance) are saved here and override the shipped config.

State lives in a writable per-user directory (``%APPDATA%`` /mission-deck on
Windows), which matters when the app is installed as a read-only single EXE.
Reads and writes are best-effort: a corrupt or missing state file never stops
the app — it just falls back to defaults.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mission_deck.config import user_config_dir

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.json"


def state_path() -> Path:
    return user_config_dir() / STATE_FILENAME


@dataclass
class AppState:
    """User preferences + recent files, persisted across launches."""

    last_config_path: str | None = None
    recent_configs: list[str] = field(default_factory=list)
    # Activated (downloaded) plugins, by stable id. A plugin that contributes a
    # nav tile only shows it in the rail while its id is listed here. Built-in
    # plugins (e.g. the tcp/http monitors) are always on and never stored.
    enabled_plugins: list[str] = field(default_factory=list)
    # Preference overrides (None / "" / 0 means "fall back to the config file").
    ping_timeout_seconds: float | None = None
    # Max simultaneous status probes (0 = use the config/built-in default). The
    # ceiling on how many connections a sweep opens at once — see network.py.
    max_concurrent_checks: int = 0
    auto_refresh_enabled: bool = False
    auto_refresh_seconds: int = 60
    browser_path: str = ""
    browser_new_window: bool = True
    appearance: str = "dark"  # "dark" | "light" | "system"
    # Dashboard / estate-wide monitoring.
    start_on_dashboard: bool = True       # open on the overview instead of a room
    dashboard_poll_enabled: bool = False  # background estate-wide status sweeps
    dashboard_poll_seconds: int = 120     # interval between background sweeps
    history_retention_days: int = 30      # prune uptime samples older than this
    # Last window geometry ("WxH+X+Y", or "zoomed"); restored on next launch.
    window_geometry: str = ""
    # Custom dashboard layout: ordered widget ids (see dashboards.WIDGETS).
    # None = never customised, so the view seeds its starter layout.
    dashboard_widgets: list[str] | None = None
    # Cloud Sync: the HTTPS source the cached cloud-config.json is pulled from,
    # and an ISO timestamp of the last successful sync (display only).
    cloud_config_url: str = ""
    cloud_last_sync: str = ""

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        """Coerce and clamp fields that could be wrong types from a stale state.json."""
        if self.ping_timeout_seconds is not None:
            try:
                self.ping_timeout_seconds = float(self.ping_timeout_seconds)
            except (TypeError, ValueError):
                self.ping_timeout_seconds = None
        try:
            self.max_concurrent_checks = max(0, int(self.max_concurrent_checks))
        except (TypeError, ValueError):
            self.max_concurrent_checks = 0
        try:
            self.auto_refresh_seconds = max(1, int(self.auto_refresh_seconds))
        except (TypeError, ValueError):
            self.auto_refresh_seconds = 60
        try:
            self.dashboard_poll_seconds = max(1, int(self.dashboard_poll_seconds))
        except (TypeError, ValueError):
            self.dashboard_poll_seconds = 120
        try:
            self.history_retention_days = max(1, int(self.history_retention_days))
        except (TypeError, ValueError):
            self.history_retention_days = 30
        for attr in ("auto_refresh_enabled", "browser_new_window",
                     "start_on_dashboard", "dashboard_poll_enabled"):
            if not isinstance(getattr(self, attr), bool):
                setattr(self, attr, bool(getattr(self, attr)))
        if not isinstance(self.browser_path, str):
            self.browser_path = ""
        if not isinstance(self.window_geometry, str):
            self.window_geometry = ""
        if self.dashboard_widgets is not None:
            if isinstance(self.dashboard_widgets, list):
                self.dashboard_widgets = [
                    str(w) for w in self.dashboard_widgets if isinstance(w, str)
                ]
            else:
                self.dashboard_widgets = None
        for attr in ("cloud_config_url", "cloud_last_sync"):
            if not isinstance(getattr(self, attr), str):
                setattr(self, attr, "")
        if self.last_config_path is not None and not isinstance(self.last_config_path, str):
            self.last_config_path = None
        if self.appearance not in ("dark", "light", "system"):
            self.appearance = "dark"
        if not isinstance(self.recent_configs, list):
            self.recent_configs = []
        else:
            self.recent_configs = [str(r) for r in self.recent_configs if isinstance(r, str)][:8]
        if not isinstance(self.enabled_plugins, list):
            self.enabled_plugins = []
        else:
            self.enabled_plugins = [str(p) for p in self.enabled_plugins if isinstance(p, str)]

    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls) -> AppState:
        path = state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            logger.debug("No saved state at %s; using defaults", path)
            return cls()
        except (OSError, ValueError) as exc:
            logger.warning("Could not read state file %s (%s); using defaults", path, exc)
            return cls()
        if not isinstance(data, dict):
            logger.warning("State file %s is not a JSON object; using defaults", path)
            return cls()
        # Only accept known keys so a future/older file can't crash construction.
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        path = state_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError as exc:
            # Never let a failed save break the app, but don't fail silently
            # either — a lost preference write is worth a log line.
            logger.warning("Could not save state to %s: %s", path, exc)

    # ------------------------------------------------------------------ #
    def remember_config(self, config_path: Path | str) -> None:
        """Record ``config_path`` as the most-recently-used config."""

        resolved = str(Path(config_path).expanduser())
        self.last_config_path = resolved
        # Move-to-front, de-duplicated, capped.
        recents = [resolved] + [r for r in self.recent_configs if r != resolved]
        self.recent_configs = recents[:8]
