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

## Architecture
- **Entrypoint:** `mission_deck/__main__.py` → `app.py:main()`
- **Module boundaries** (well-defined, preserve them):
  - `config.py` — find/load/structurally validate JSON
  - `models.py` — typed `Site`/`Room`/`Device` models + `@register_device` registry
  - `network.py` — async TCP status checks (runs on worker thread); also `http_get`/`tcp_send` for control actions
  - `controls.py` — `DeviceControl` objects from config `commands` entries + built-in "Open Web UI"
  - `browser.py` — launch URLs in configured browser via subprocess (Chromium gets `--new-window`)
  - `state.py` — persisted `AppState` (recent configs, settings overrides)
  - `theme.py` — color palette + sizing tokens
  - `app.py` — CustomTkinter GUI (~1300 loc)

## Code conventions
- Python 3.11+, `from __future__ import annotations`, full type hints.
- Data classes use `@dataclass(slots=True)`.
- Validation at the edges: raise `ConfigError`/`DeviceConfigError` with human-readable messages.
- No third-party deps beyond `customtkinter` and `pillow` without discussion.
- Device types registered via `@register_device("type_name")` in `models.py`.

## Adding a new device type
One `@register_device` decorator in `models.py` — no other changes needed. The type alias string(s) you pass are the config `type` values that map to the new class.

## Packaging notes
- `mission-deck.spec` bundles `config.example.json` and `customtkinter` data files.
- PyInstaller `console=False` produces a windowed EXE (no console window).
- Set `.ico` path in the spec's `EXE(icon=...)` to brand the EXE.

## Gotchas
- **No CI, no linter, no formatter, no test runner configured.** No standard to enforce, but `ruff` cache is gitignored suggesting prior use.
- Network checks run on a background thread; results marshalled to Tk thread via `widget.after(0, ...)`.
- Device control commands run on a background thread too (blocking).
- The project is early-stage.
