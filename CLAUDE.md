# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**mission-deck** is a Python/CustomTkinter desktop app for managing courtroom AV equipment. It replaces a legacy PowerShell GUI script, letting AV techs monitor device status and open web UIs for every device in a room in one click. All environment data (IPs, device names) lives in a local, git-ignored `config.json`. The repo ships `config.example.json` with dummy data only — never commit real host data.

## Commands

```powershell
# Set up and run from source
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:MISSION_DECK_CONFIG = "$PWD\config.example.json"
python -m mission_deck

# Build single EXE
pip install -r requirements-dev.txt
pyinstaller mission-deck.spec
# Output: dist\mission-deck.exe
```

No automated test suite exists. Manual verification against `config.example.json` is the current standard.

## Architecture

### Module boundaries

Each module has one job and strict constraints:

| Module | Job | Key constraint |
|--------|-----|----------------|
| `config.py` | Locate/load/validate JSON config | No GUI, no device internals |
| `models.py` | Typed data models + device registry | No I/O, no GUI, no networking |
| `network.py` | Async TCP probes + HTTP/TCP control transports | No GUI; thread-safe; results via callback |
| `controls.py` | Build per-device control action lists | Config-driven; delegates I/O to `network.py` |
| `browser.py` | Open URLs in configured browser | Launches subprocess/webbrowser; no models |
| `state.py` | Persisted user preferences + recent files | Best-effort JSON; never breaks app if corrupt |
| `theme.py` | Colour and sizing constants | Pure constants, no logic |
| `logging_setup.py` | Centralised diagnostic + audit logging | Stdlib only; idempotent; never raises into callers |
| `app.py` | CustomTkinter UI + event orchestration | ~1300 LOC; marshals network results to UI thread |

### Data model hierarchy

```
Site
  └── Room[]
        └── Device (base, dispatched via registry decorator)
              ├── PTZCamera, ControlProcessor, AudioDSP, Display, DocumentCamera, Recorder
              ├── VideoMatrix, VideoEncoder (TX), VideoDecoder (RX)  — AV-over-IP (Blustream, Crestron NVX, …)
              ├── VideoConferenceCodec  — VC codecs (Cisco Webex, Poly, …)
              └── GenericDevice (fallback for unknown types)
```

`Device.from_dict()` dispatches to subclasses registered with `@register_device("type_key")`. Unknown JSON keys are preserved in `Device.extra` for forward compatibility.

### Threading model

- **UI thread:** Tk runs exclusively here. Never touch widgets from a worker thread.
- **Status-check worker:** `run_status_checks()` runs on a daemon thread, probes devices concurrently via `asyncio`, publishes `CheckResult` objects to a thread-safe queue.
- **Control worker:** Device commands (HTTP/TCP) run on a daemon thread via `app.run_background()`.
- **Result marshalling:** `app.after()` polls the queue on the Tk timer; results update device cards live without blocking.

### Config discovery order (first match wins)

1. `MISSION_DECK_CONFIG` env var
2. `config.json` in CWD
3. `config.json` next to the executable
4. Per-user dir (`%APPDATA%\mission-deck` on Windows)

If no config is found, a `WelcomeWindow` is shown. Switching config in Settings triggers a soft-restart (destroy App → re-run discovery loop).

### Config-driven device commands

Device `commands` entries in JSON (HTTP or raw TCP) require no code changes. Placeholders `{host}`, `{port}`, `{value}` are substituted at runtime (in both the URL and the body). An optional `prompt` key causes the UI to ask the user for `{value}` before sending. HTTP commands may set `method` (e.g. `POST`), a `body`, `headers`, `auth` (`{"username","password"}` → Basic auth) and `verify_tls: false` — enough to drive VC codec APIs (Cisco xCommand, Poly REST) over self-signed-cert HTTPS. Codec credentials live in the git-ignored `config.json`, never in `config.example.json`.

### Widget pooling

`DeviceCard` and `RoomButton` widgets are pooled and rebound via `set_device()` rather than recreated. `CityGroup` builds room buttons lazily on first expand. This keeps navigation snappy at 100+ rooms.

## Key conventions

- Python 3.11+, full type hints, `from __future__ import annotations`
- `@dataclass(slots=True)` on all model classes
- Validation errors raised early as typed exceptions (`ConfigError`, `DeviceConfigError`, `ControlError`) with human-readable messages — never raw `Exception`
- Networking uses `asyncio` + stdlib `socket` only; no third-party HTTP library
- `config.json` is `.gitignore`d; `config.example.json` contains dummy data only

### Logging & auditing

`logging_setup.setup_logging()` is called once at the top of `main()` and writes two
rotating streams to `<user_config_dir>/logs/` (override the directory with
`MISSION_DECK_LOG_DIR`):

- **`mission-deck.log`** — diagnostic. Every module logs via
  `logging.getLogger(__name__)`. Level defaults to `INFO`; set
  `MISSION_DECK_LOG_LEVEL=DEBUG` to trace probes/transports.
- **`audit.log`** — one JSON object per line recording operator actions
  (`device.command`, `room.open_web_uis`, `status_check.complete`, `config.load`,
  `config.save`, `config.switch`, `settings.change`, `app.start`/`app.stop`). Emit
  events with `logging_setup.audit(event, **fields)`; each line carries `ts`, `event`
  and `user`. The audit logger never propagates to the diagnostic handlers.

Conventions: `models.py`/`theme.py` stay logging-free (purity constraint) — their
typed exceptions are logged by callers. Uncaught exceptions on the main thread and
worker threads are captured to the diagnostic log via installed excepthooks, so a
background crash is never silently lost. Auditing is best-effort and must never raise
into the caller's control flow.
