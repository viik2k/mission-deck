"""Configuration discovery, loading and structural validation for mission-deck.

This module is intentionally free of any GUI or networking concerns. Its only
job is to locate a ``config.json`` on disk, parse it, and confirm it is
*structurally* well-formed (correct JSON shape, required keys present).

Rich per-device parsing into typed objects is handled separately by the data
models (see ``models.py``, added in Step 2). Keeping the two apart means the
GUI can:

  * discover whether a config exists at startup, and
  * fall back to prompting the user to pick a file when one is missing,

without needing to know anything about device internals.

Environment data (real IPs, device names) lives only in ``config.json`` which
is git-ignored. The repository ships ``config.example.json`` with dummy data.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The schema version this build understands. Bump when the on-disk format
# changes in a backwards-incompatible way.
SUPPORTED_SCHEMA_VERSION = 1

CONFIG_FILENAME = "config.json"
EXAMPLE_CONFIG_FILENAME = "config.example.json"


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class ConfigError(Exception):
    """Base class for all configuration-related problems."""


class ConfigNotFoundError(ConfigError):
    """No ``config.json`` could be located in any of the search paths.

    The GUI treats this as a *recoverable* condition: it prompts the user to
    select a config file rather than aborting.
    """

    def __init__(self, searched: list[Path]) -> None:
        self.searched = searched
        locations = "\n  ".join(str(p) for p in searched) or "(none)"
        super().__init__(
            "Could not find a config.json. Searched the following locations:\n  "
            f"{locations}"
        )


class ConfigParseError(ConfigError):
    """The file exists but is not valid JSON."""


class ConfigValidationError(ConfigError):
    """The file is valid JSON but does not match the expected structure."""


# --------------------------------------------------------------------------- #
# Path discovery
# --------------------------------------------------------------------------- #
def _app_root() -> Path:
    """Best-effort directory that holds the running application.

    Works both when running from source and when frozen by PyInstaller.
    """

    if getattr(sys, "frozen", False):  # PyInstaller bundle
        return Path(sys.executable).resolve().parent
    # mission_deck/config.py -> repo root is one level up from the package.
    return Path(__file__).resolve().parent.parent


def user_config_dir() -> Path:
    """Per-user, writable config/state directory, following OS conventions.

    This is where the app stores its remembered settings (``state.json``) and a
    good default home for a user-managed ``config.json`` — important once the
    app is packaged as a single EXE living in a read-only Program Files folder.
    """

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:  # Linux / other POSIX
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "mission-deck"


# Backwards-compatible alias (kept private name used internally).
_user_config_dir = user_config_dir


def resource_path(name: str) -> Path:
    """Locate a bundled read-only resource, working under PyInstaller.

    When frozen with ``--onefile``, data files are unpacked to ``sys._MEIPASS``;
    otherwise they sit next to the app. Used for the shipped
    ``config.example.json`` template.
    """

    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = Path(base) / name
        if candidate.exists():
            return candidate
    return _app_root() / name


def candidate_config_paths() -> list[Path]:
    """Ordered list of locations to search for ``config.json``.

    Order (highest priority first):
      1. ``MISSION_DECK_CONFIG`` environment variable, if set.
      2. The current working directory.
      3. The application/repository root.
      4. The per-user config directory.
    """

    candidates: list[Path] = []

    env_override = os.environ.get("MISSION_DECK_CONFIG")
    if env_override:
        candidates.append(Path(env_override).expanduser())

    candidates.append(Path.cwd() / CONFIG_FILENAME)
    candidates.append(_app_root() / CONFIG_FILENAME)
    candidates.append(_user_config_dir() / CONFIG_FILENAME)

    # De-duplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in candidates:
        resolved = path.expanduser()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def find_config() -> Path | None:
    """Return the first existing config path, or ``None`` if none exist."""

    for path in candidate_config_paths():
        if path.is_file():
            return path
    return None


def example_config_path() -> Path:
    """Location of the bundled dummy-data example config (PyInstaller-aware)."""

    return resource_path(EXAMPLE_CONFIG_FILENAME)


# --------------------------------------------------------------------------- #
# Loading + structural validation
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class LoadedConfig:
    """Raw, validated-but-untyped config plus the path it came from.

    ``data`` is the parsed JSON object. Step 2's data models consume this to
    build typed Room/Device objects; keeping it raw here avoids a circular
    dependency between config loading and the models.
    """

    path: Path
    data: dict[str, Any]

    @property
    def schema_version(self) -> int:
        return int(self.data.get("schema_version", 0))

    @property
    def rooms(self) -> list[dict[str, Any]]:
        return list(self.data.get("rooms", []))

    @property
    def app_settings(self) -> dict[str, Any]:
        return dict(self.data.get("app", {}))


def load_config(path: str | os.PathLike[str] | None = None) -> LoadedConfig:
    """Load and structurally validate a config file.

    Parameters
    ----------
    path:
        Explicit file to load. When ``None``, the standard search paths are
        consulted via :func:`find_config`.

    Raises
    ------
    ConfigNotFoundError
        No file given and none found in the search paths.
    ConfigParseError
        The file is not valid JSON.
    ConfigValidationError
        The JSON does not match the expected structure or schema version.
    """

    if path is None:
        found = find_config()
        if found is None:
            raise ConfigNotFoundError(candidate_config_paths())
        config_path = found
    else:
        config_path = Path(path).expanduser()
        if not config_path.is_file():
            raise ConfigNotFoundError([config_path])

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Unable to read '{config_path}': {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ConfigParseError(
            f"'{config_path}' is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})"
        ) from exc

    _validate_structure(data, config_path)
    return LoadedConfig(path=config_path, data=data)


def save_config(path: str | os.PathLike[str], data: dict[str, Any]) -> Path:
    """Write a config dict to ``path`` as pretty JSON, atomically.

    The data is structurally validated first (so a UI bug can never persist a
    malformed config), then written to a sibling ``*.tmp`` file and
    :func:`os.replace`-d into place — meaning a crash mid-write can't leave a
    truncated config on disk.

    Returns the resolved path written to.

    Raises
    ------
    ConfigValidationError
        The data does not match the expected structure.
    ConfigError
        The file could not be written.
    """

    target = Path(path).expanduser()
    _validate_structure(data, target)

    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise ConfigError(f"Unable to save '{target}': {exc}") from exc
    return target


def _validate_structure(data: Any, source: Path) -> None:
    """Confirm the top-level shape of the config. Cheap, not exhaustive.

    Deep per-device validation belongs to the data models; here we only verify
    enough that downstream code can rely on ``rooms`` being a list of objects.
    """

    if not isinstance(data, dict):
        raise ConfigValidationError(
            f"'{source}': top-level value must be a JSON object, "
            f"got {type(data).__name__}."
        )

    version = data.get("schema_version")
    if version is None:
        raise ConfigValidationError(f"'{source}': missing required 'schema_version'.")
    if not isinstance(version, int):
        raise ConfigValidationError(
            f"'{source}': 'schema_version' must be an integer, got {version!r}."
        )
    if version != SUPPORTED_SCHEMA_VERSION:
        raise ConfigValidationError(
            f"'{source}': unsupported schema_version {version}. "
            f"This build supports version {SUPPORTED_SCHEMA_VERSION}."
        )

    rooms = data.get("rooms")
    if not isinstance(rooms, list):
        raise ConfigValidationError(
            f"'{source}': 'rooms' must be a list, "
            f"got {type(rooms).__name__ if rooms is not None else 'missing'}."
        )
    for index, room in enumerate(rooms):
        if not isinstance(room, dict):
            raise ConfigValidationError(
                f"'{source}': rooms[{index}] must be a JSON object, "
                f"got {type(room).__name__}."
            )
        if not isinstance(room.get("devices", []), list):
            raise ConfigValidationError(
                f"'{source}': rooms[{index}].devices must be a list."
            )
