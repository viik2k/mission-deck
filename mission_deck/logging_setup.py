"""Centralised logging and audit trail for mission-deck.

This module is the single place that configures logging for the whole app. It
is deliberately small and dependency-free (stdlib only) and is safe to import
from anywhere — configuring handlers is an explicit, idempotent step performed
once at startup by :func:`setup_logging`.

Two distinct streams are produced, in keeping with how an operations team
actually uses them:

* **Diagnostic log** (``mission-deck.log``) — the developer/support view. Full
  detail at ``DEBUG`` and up, including stack traces for anything unexpected.
  Modules obtain a logger with ``logging.getLogger(__name__)`` as usual.

* **Audit log** (``audit.log``) — the compliance/operations view. One JSON
  object per line recording *who did what, to which device, when, and with what
  outcome*. AV techs operate courtroom equipment, so a tamper-evident-ish,
  append-only record of every command issued is the whole point. Emit events
  with :func:`audit`.

Both files live in a writable per-user directory and rotate, so the app can be
shipped as a read-only single EXE without ever running the disk out of space.

Configuration (all optional, read from the environment):

* ``MISSION_DECK_LOG_DIR``   — override the directory both logs are written to.
* ``MISSION_DECK_LOG_LEVEL`` — diagnostic file/console level (default ``INFO``;
  e.g. ``DEBUG`` when chasing a bug).
"""

from __future__ import annotations

import getpass
import json
import logging
import logging.handlers
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mission_deck import __version__
from mission_deck.config import user_config_dir

# Names kept as module constants so tests and packaging can reference them.
LOG_DIRNAME = "logs"
DIAGNOSTIC_FILENAME = "mission-deck.log"
AUDIT_FILENAME = "audit.log"
AUDIT_LOGGER_NAME = "mission_deck.audit"

# Rotate at ~2 MB, keeping a handful of generations. Plenty for a desktop tool,
# trivial on disk, and survives a packaged install in Program Files.
_MAX_BYTES = 2 * 1024 * 1024
_BACKUP_COUNT = 5

_DIAGNOSTIC_FORMAT = (
    "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"
)

# Set once; guards against double-configuring handlers (e.g. a soft restart that
# re-enters main()).
_configured = False
_log_dir: Path | None = None

logger = logging.getLogger(__name__)
_audit_logger = logging.getLogger(AUDIT_LOGGER_NAME)


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
def _resolve_log_dir() -> Path:
    override = os.environ.get("MISSION_DECK_LOG_DIR")
    if override:
        return Path(override).expanduser()
    return user_config_dir() / LOG_DIRNAME


def log_location() -> Path | None:
    """The directory logs are being written to, or ``None`` before setup /
    when no writable location was available. For UI "open logs" affordances."""

    return _log_dir


def _resolve_level() -> int:
    name = (os.environ.get("MISSION_DECK_LOG_LEVEL") or "INFO").strip().upper()
    return logging.getLevelName(name) if name in logging._nameToLevel else logging.INFO


def setup_logging(level: int | None = None) -> Path | None:
    """Configure diagnostic + audit logging. Idempotent; safe to call again.

    Returns the directory logs are written to, or ``None`` if no writable
    location was available (in which case console logging still works so the app
    is never crippled by an unwritable disk).
    """

    global _configured, _log_dir
    if _configured:
        return _log_dir

    diag_level = level if level is not None else _resolve_level()

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers decide what actually gets emitted.

    # Console handler — surfaces problems during dev/headless runs. A windowed
    # PyInstaller build has no console (``sys.stderr is None``), so guard for it.
    if sys.stderr is not None:
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(diag_level)
        console.setFormatter(logging.Formatter(_DIAGNOSTIC_FORMAT))
        root.addHandler(console)

    log_dir = _resolve_log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Disk/permissions problem: keep console logging, give up on files.
        _configured = True
        _log_dir = None
        logger.warning("Could not create log directory %s: %s", log_dir, exc)
        _install_excepthooks()
        return None

    # Diagnostic file — everything, for support/debugging.
    diag_handler = logging.handlers.RotatingFileHandler(
        log_dir / DIAGNOSTIC_FILENAME,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    diag_handler.setLevel(diag_level)
    diag_handler.setFormatter(logging.Formatter(_DIAGNOSTIC_FORMAT))
    root.addHandler(diag_handler)

    # Audit file — structured JSON lines, INFO only, never propagated to the
    # diagnostic handlers (the two streams are kept clean and separate).
    audit_handler = logging.handlers.RotatingFileHandler(
        log_dir / AUDIT_FILENAME,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    audit_handler.setLevel(logging.INFO)
    audit_handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False
    _audit_logger.addHandler(audit_handler)

    _configured = True
    _log_dir = log_dir
    _install_excepthooks()
    logger.info(
        "Logging started (mission-deck %s, level %s) -> %s",
        __version__,
        logging.getLevelName(diag_level),
        log_dir,
    )
    return log_dir


def log_dir() -> Path | None:
    """Directory logs are written to, or ``None`` if file logging is disabled."""

    return _log_dir


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
def current_user() -> str:
    """The operator name recorded against audit events (the OS login)."""

    try:
        return getpass.getuser()
    except Exception:  # getuser can raise if no username is resolvable
        return "unknown"


def audit(event: str, **fields: Any) -> None:
    """Append one structured audit event (who/what/when) as a JSON line.

    ``event`` is a short dotted action name (e.g. ``"device.command"``). Extra
    keyword fields capture the specifics — device id, target host, outcome, etc.
    Values are coerced to JSON-safe form; auditing must never raise into the
    caller's control flow.
    """

    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        "user": current_user(),
    }
    for key, value in fields.items():
        record[key] = value if _json_safe(value) else str(value)
    try:
        _audit_logger.info(json.dumps(record, ensure_ascii=False, default=str))
    except Exception:  # pragma: no cover - auditing is best-effort
        logger.exception("Failed to write audit event %r", event)


def _json_safe(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool, type(None), list, dict))


def audit_log_path() -> Path | None:
    """Path to the current audit log, or ``None`` if file logging is disabled."""

    return (_log_dir / AUDIT_FILENAME) if _log_dir is not None else None


# Bytes read per backward step when tailing the audit log (see ``tail_audit``).
_TAIL_BLOCK = 64 * 1024


def tail_audit(limit: int = 20) -> list[dict[str, Any]]:
    """Return up to ``limit`` most-recent audit events, newest first.

    Reads only the *tail* of the JSON-lines audit log: it seeks to the end and
    walks backwards a block at a time until it has enough lines, so a rotated
    multi-MB log is never pulled into memory in full (only the last few KB).
    Best-effort and read-only: a missing file, an I/O error, or a malformed line
    is simply skipped — this must never raise into the UI.
    """

    if limit <= 0:
        return []
    path = audit_log_path()
    if path is None:
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            pos = handle.tell()
            data = b""
            newlines = 0
            # Stop once we've seen one more newline than requested (so the first
            # kept line is whole, not a fragment) or reached the start of file.
            while pos > 0 and newlines <= limit:
                step = min(_TAIL_BLOCK, pos)
                pos -= step
                handle.seek(pos)
                chunk = handle.read(step)
                data = chunk + data
                newlines += chunk.count(b"\n")
    except OSError as exc:
        logger.debug("Could not read audit log %s: %s", path, exc)
        return []

    events: list[dict[str, Any]] = []
    for raw in reversed(data.split(b"\n")):
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(record, dict):
            events.append(record)
        if len(events) >= limit:
            break
    return events


# --------------------------------------------------------------------------- #
# Global crash capture
# --------------------------------------------------------------------------- #
def _install_excepthooks() -> None:
    """Route otherwise-unhandled exceptions (main and worker threads) to the log.

    Without this, an exception escaping a daemon worker thread is printed to a
    stderr nobody reads in a packaged GUI build, then silently lost. Here every
    crash leaves a full traceback in the diagnostic log.
    """

    def _main_hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("mission_deck").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _main_hook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        logging.getLogger("mission_deck").error(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = _thread_hook
