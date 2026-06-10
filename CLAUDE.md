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
| `network.py` | Async device probes (monitor registry) + HTTP/TCP control transports | No GUI; thread-safe; results via callback |
| `controls.py` | Build per-device control action lists | Config-driven; delegates I/O to `network.py` |
| `browser.py` | Open URLs in configured browser | Launches subprocess/webbrowser; no models |
| `history.py` | Persisted uptime-history store (SQLite) | Stdlib `sqlite3` only; best-effort; never raises into callers |
| `dashboard.py` | Estate-wide overview view (flat report: stat strip + attention/recorders/uptime/activity sections) | Presentation only; reads `Site`/`HistoryStore`; no networking; UI thread |
| `report.py` | Estate status report export (CSV: per-device status, latency, 24h uptime) | Pure data-out; no GUI/networking; UI picks the path and runs it off the Tk thread |
| `state.py` | Persisted user preferences + recent files | Best-effort JSON; never breaks app if corrupt |
| `theme.py` | Colour and sizing constants | Pure constants, no logic |
| `ui.py` | Cached shared fonts, button/switch style tokens, `PromptDialog` | All views use `ui.font()` (never raw `CTkFont`), the `BTN_*`/`SWITCH` tokens, and `PromptDialog` (never `CTkInputDialog`); cache resets per Tk root |
| `logging_setup.py` | Centralised diagnostic + audit logging | Stdlib only; idempotent; never raises into callers |
| `icons.py` | Vector icon factory (CTkImage) | Presentation only |
| `plugins.py` | Plugins screen + plugin catalogue (`PluginSpec`) | Presentation + static catalogue; activation state lives in `AppState` |
| `cloud.py` | Cloud Sync view (config-source preview, plugin-gated) | Presentation only; no real Graph client yet |
| `editors.py` | Room / device / command editor dialogs | Presentation; writes back through `App` save paths |
| `app.py` | CustomTkinter UI + event orchestration | Marshals network results to UI thread |

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

### Monitoring plugins (monitor registry)

How a device's reachability is judged is itself pluggable. `network.py` holds a
`_MONITOR_REGISTRY` and a `@register_monitor("name")` decorator that mirrors
`@register_device`. A *monitor* is `async (Device, float) -> CheckResult`.
Built-ins: `tcp` (open a TCP connection — the historical default for every
config), `http`/`https` (any *answered* endpoint counts as up; URL comes from
a `health_url` config key, else the device's `web_url`) and `ping`/`icmp` (one
ICMP echo via the system ping binary — for devices with no open TCP port;
on Windows a reply only counts when it carries a TTL). `check_device()` is just
a dispatcher: a device opts into a monitor with a `"monitor"` config key,
otherwise it falls back to `DEFAULT_MONITOR` (`tcp`). Add a new check type with a
single decorator — no caller changes.

### Dashboard + estate-wide sweep

The **Overview** (`dashboard.py`) is a second top-level view, swapped with the
room panel in the same grid cell (`App.show_dashboard()` / `show_room_view()`;
sidebar "⌂ Overview" button). It is deliberately **not** a bento grid of boxed
cards: it renders as a flat, single-column report — a bare stat strip (rooms /
devices / online / offline / healthy), then hairline-ruled sections for
attention (offline devices), recorders, per-room 24h uptime and a
recent-activity feed (`logging_setup.tail_audit`). It is pure presentation: it
reads live `Site` state + the `HistoryStore` and calls back into `App`; it
never touches the network itself. The destroy-and-rebuild list sections are
guarded by content signatures and capped at `MAX_LIST_ROWS`, so a refresh whose
data hasn't changed (or an estate with hundreds of offline devices) doesn't
rebuild thousands of widgets. The overview topbar also offers **EXPORT** — a
CSV status report via `report.py` (written off the Tk thread; audited as
`report.export`).

`App.run_estate_sweep()` probes **all** rooms (`site.all_devices()`) using the
same worker/queue/`after()` pattern as a room check but on its own queue +
generation counter. Each completed sweep (and each single-room check) appends a
batch of `history.Sample`s, persisted off the UI thread via `run_background`. A
background timer (`dashboard_poll_enabled`/`dashboard_poll_seconds` in `AppState`)
can re-run the sweep on an interval.

### Uptime history

`history.HistoryStore` is a best-effort SQLite store at
`<user_config_dir>/history.db` (override with `MISSION_DECK_HISTORY_DB`). One
shared connection (`check_same_thread=False` + a lock, WAL mode) serves Tk-thread
reads and worker-thread writes. Only resolved (ONLINE/OFFLINE) samples are
stored; `uptime`/`room_uptime` return `None` when the window has no data (so the
UI shows "—", not a false 0%). Samples older than `history_retention_days` are
pruned on open. Like `state.py`/auditing, it never raises into callers.

### Threading model

- **UI thread:** Tk runs exclusively here. Never touch widgets from a worker thread.
- **Status-check worker:** `run_status_checks()` runs on a daemon thread, probes devices concurrently via `asyncio` (each device through its registered monitor), publishes `CheckResult` objects to a thread-safe queue. Used for both the per-room check and the estate-wide sweep (separate queues/generations). Concurrency is **bounded** by an `asyncio.Semaphore` (default `network.DEFAULT_MAX_CONCURRENCY = 128`, tunable via the `max_concurrent_checks` app/config setting → `App._effective_concurrency()`) so an estate-wide sweep of thousands of devices never opens thousands of sockets at once. The Tk-side queue drain (`_drain_results`) applies results to the model in a batch and repaints **once per tick**, not per device.
- **Control worker:** Device commands (HTTP/TCP) run on a daemon thread via `app.run_background()`.
- **History writes:** batched per sweep/check and written off the UI thread via `run_background` (SQLite, best-effort).
- **Result marshalling:** `app.after()` polls the queue on the Tk timer; results update device cards / sidebar dots live without blocking.

### Config discovery order (first match wins)

1. `MISSION_DECK_CONFIG` env var
2. `config.json` in CWD
3. `config.json` next to the executable
4. Per-user dir (`%APPDATA%\mission-deck` on Windows)

If no config is found, a `WelcomeWindow` is shown. Switching config in Settings triggers a soft-restart (destroy App → re-run discovery loop).

### Config-driven device commands

Device `commands` entries in JSON (HTTP or raw TCP) require no code changes. Placeholders `{host}`, `{port}`, `{value}` are substituted at runtime (in both the URL and the body). An optional `prompt` key causes the UI to ask the user for `{value}` before sending. HTTP commands may set `method` (e.g. `POST`), a `body`, `headers`, `auth` (`{"username","password"}` → Basic auth) and `verify_tls: false` — enough to drive VC codec APIs (Cisco xCommand, Poly REST) over self-signed-cert HTTPS. Codec credentials live in the git-ignored `config.json`, never in `config.example.json`.

A device may also pick how it is health-checked with a `"monitor"` key (`tcp` default, or `http`/`https`); the `http` monitor probes an optional `health_url` (else the device's web UI). See the monitor registry above.

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
  (`device.command`, `room.open_web_uis`, `status_check.complete`,
  `status_check.estate`, `config.load`, `config.save`, `config.switch`,
  `settings.change`, `report.export`, `app.start`/`app.stop`). Emit
  events with `logging_setup.audit(event, **fields)`; each line carries `ts`, `event`
  and `user`. The audit logger never propagates to the diagnostic handlers.

Conventions: `models.py`/`theme.py` stay logging-free (purity constraint) — their
typed exceptions are logged by callers. Uncaught exceptions on the main thread and
worker threads are captured to the diagnostic log via installed excepthooks, so a
background crash is never silently lost. Auditing is best-effort and must never raise
into the caller's control flow.
