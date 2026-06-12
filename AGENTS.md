# mission-deck — agent guide

## Quick start
```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:MISSION_DECK_CONFIG = "$PWD\config.example.json"
python -m mission_deck
```

## Commands
| Action | Command |
|---|---|
| Run from source | `python -m mission_deck` |
| Install dev deps | `pip install -r requirements-dev.txt` |
| Build EXE | `pyinstaller mission-deck.spec` (no `build.ps1` exists) |
| Test | No test framework yet — manual verification with `config.example.json` |

## Config
- Real infra data lives in **git-ignored** `config.json` — never commit it.
- The repo ships only `config.example.json` (dummy data).
- Config discovery order: `MISSION_DECK_CONFIG` env var → cwd → app root → `%APPDATA%/mission-deck/` (Win) or `~/.config/mission-deck/` (Linux) / `~/Library/Application Support/mission-deck/` (macOS).
- Schema version must be `1`. Unknown device types fall back to `GenericDevice` (don't break loading).
- Per-user GUI preferences persist in `state.json` at the same per-user dir.
- Uptime history is a best-effort SQLite DB (`history.db`) in the per-user dir; override with `MISSION_DECK_HISTORY_DB`.
- Logs: `mission-deck.log` (diagnostic) + `audit.log` (JSON-per-line operator actions) under `<user dir>/logs/`; override dir with `MISSION_DECK_LOG_DIR`, level with `MISSION_DECK_LOG_LEVEL`.

## Architecture
- **Entrypoint:** `mission_deck/__main__.py` → `app.py:main()`
- **Module boundaries** (well-defined, preserve them):
  - `config.py` — find/load/structurally validate JSON
  - `models.py` — typed `Site`/`Room`/`Device` models + `@register_device` registry
  - `network.py` — async status checks (worker thread) via a pluggable **monitor registry** (`@register_monitor`: `tcp` default, `http`/`https`), bounded by a concurrency semaphore; also `http_request`/`http_get`/`tcp_send` for control actions
  - `controls.py` — `DeviceControl` objects from config `commands` entries + built-in "Open Web UI"
  - `browser.py` — launch URLs in configured browser via subprocess (Chromium gets `--new-window`)
  - `history.py` — best-effort SQLite uptime-history store (samples per sweep; per-device/per-room uptime %)
  - `dashboard.py` — estate-wide Overview view (KPIs, attention/recorders/uptime/activity panels); pure presentation
  - `state.py` — persisted `AppState` (recent configs, settings overrides, dashboard poll prefs)
  - `theme.py` — color palette + sizing tokens
  - `logging_setup.py` — diagnostic + audit logging; installs crash-capturing excepthooks
  - `editors.py` — in-app room/device/command editor dialogs (go through `models`/`config` validation)
  - `palette.py` — global command palette (Ctrl+K): fuzzy jump to rooms/devices/actions; presentation only
  - `app.py` — CustomTkinter GUI + orchestration (top nav bar, room view, Overview, estate sweep, keyboard shortcuts)

## Code conventions
- Python 3.11+, `from __future__ import annotations`, full type hints.
- Data classes use `@dataclass(slots=True)`.
- Validation at the edges: raise `ConfigError`/`DeviceConfigError` with human-readable messages.
- No third-party deps beyond `customtkinter` and `pillow` without discussion.
- Device types registered via `@register_device("type_name")` in `models.py`.

## Adding a new device type
One `@register_device` decorator in `models.py` — no other changes needed. The type alias string(s) you pass are the config `type` values that map to the new class.

## Adding a new monitor (status-check type)
One `@register_monitor("name")` decorator in `network.py` — an `async (Device, float) -> CheckResult`. A device opts in with a `"monitor"` config key; otherwise it falls back to `DEFAULT_MONITOR` (`tcp`). No caller changes needed.

## Packaging notes
- `mission-deck.spec` bundles `config.example.json` and `customtkinter` data files.
- PyInstaller `console=False` produces a windowed EXE (no console window).
- Set `.ico` path in the spec's `EXE(icon=...)` to brand the EXE.

## Gotchas
- **No CI, no linter, no formatter, no test runner configured.** No standard to enforce, but `ruff` cache is gitignored suggesting prior use.
- Network checks run on a background thread; results marshalled to Tk thread via `widget.after(0, ...)`. A **generation counter** discards stale results when the room/run changes.
- The estate-wide sweep (`run_estate_sweep`) reuses the same worker/queue pattern on its own queue + generation; concurrency is bounded by `network.DEFAULT_MAX_CONCURRENCY` (128), tunable via `max_concurrent_checks`.
- Device control commands run on a background thread too (blocking).
- `models.py`/`theme.py` stay logging-free (purity constraint); their typed exceptions are logged by callers.
- The project is early-stage.
