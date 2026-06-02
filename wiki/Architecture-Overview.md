# Architecture Overview

This page explains how mission-deck is put together: its module boundaries, the
data-model hierarchy, the threading model, and the startup/config-discovery
flow. It's aimed at **developers** and anyone maintaining the codebase.

For how to *extend* the code, see the [Developer Guide](Developer-Guide.md). For
the network internals, see
[Networking & Device Control](Networking-and-Device-Control.md).

---

## Design philosophy

Three principles run through the whole codebase:

1. **Separation of logic from environment data.** No IPs or device names live in
   the repo. Application code is generic; all site-specific data is loaded at
   runtime from a git-ignored `config.json`. (See the
   [Security Model](Security-Model.md).)

2. **Strict module boundaries.** Each module has exactly one job and a short
   list of things it must *not* do. This keeps the code testable and the
   responsibilities obvious.

3. **Validation at the edges, never raw exceptions.** Bad config is caught early
   and re-raised as a typed, human-readable exception (`ConfigError`,
   `DeviceConfigError`, `ControlError`) that the UI can show plainly — never a
   stack trace deep in a widget callback.

---

## Module map

```
mission_deck/
├── __init__.py        # version / app metadata (__version__ = "0.1.0")
├── __main__.py        # `python -m mission_deck` → app.main()
├── config.py          # locate / load / structurally validate JSON
├── models.py          # Site → Room → Device typed models + registry
├── network.py         # async status probes + HTTP/TCP control transports
├── controls.py        # build per-device control action lists
├── browser.py         # open URLs in the configured browser
├── state.py           # persisted per-user preferences + recent files
├── theme.py           # colour palette + sizing tokens
├── logging_setup.py   # diagnostic + audit logging
├── editors.py         # in-app room/device/command editor dialogs
└── app.py             # CustomTkinter UI + event orchestration (~1900 LOC)
```

### Responsibilities and constraints

| Module | Job | Must NOT |
|--------|-----|----------|
| `config.py` | Find a config file, parse JSON, validate top-level shape; atomic save. | Touch the GUI or device internals. |
| `models.py` | Turn raw config into typed `Site`/`Room`/`Device` objects; device registry. | Do I/O, GUI, or networking. Stays logging-free (purity). |
| `network.py` | Async TCP reachability probes; blocking HTTP/TCP control transports; recording-status fetch. | Touch the GUI. Must be thread-safe; results via callback. |
| `controls.py` | Build `DeviceControl` action lists from config `commands` + built-in "Open Web UI". | Do the actual I/O itself (delegates to `network.py`). |
| `browser.py` | Launch URLs via subprocess/`webbrowser`. | Import models or touch the GUI. |
| `state.py` | Load/save per-user `state.json` (best-effort). | Break the app if the file is missing/corrupt. |
| `theme.py` | Pure colour/size constants. | Contain logic. Stays logging-free. |
| `logging_setup.py` | Configure diagnostic + audit logging; install excepthooks. | Use anything beyond stdlib; raise into callers. |
| `editors.py` | Tkinter dialogs for editing rooms/devices/commands. | Bypass `models`/`config` validation. |
| `app.py` | The CustomTkinter UI; marshals worker results onto the Tk thread. | Hold business rules that belong in `models`. |

The dependency direction is roughly:

```
app.py ── editors.py
  │  ├── controls.py ── network.py ── models.py ── config.py
  │  ├── browser.py
  │  ├── state.py ──────────────────────────────── config.py
  │  ├── theme.py ── models.py
  │  └── logging_setup.py ───────────────────────── config.py
```

`config.py` and `models.py` sit at the bottom and import nothing from the rest
of the app, which keeps the data layer reusable and easy to reason about.

---

## Data-model hierarchy

```
Site                       # all rooms + app settings
  └── Room                 # one courtroom
        └── Device         # base class, dispatched via the type registry
              ├── PTZCamera, ControlProcessor, AudioDSP, Display,
              │   DocumentCamera, Recorder
              ├── VideoMatrix, VideoEncoder (TX), VideoDecoder (RX)
              ├── VideoConferenceCodec
              └── GenericDevice   # fallback for unknown types
```

All model classes are `@dataclass(slots=True)`.

### How dispatch works

- `Device.from_dict()` reads the `type` field, lowercases it, and looks it up in
  the **device registry** (`_DEVICE_REGISTRY`), instantiating the matching
  subclass — or `GenericDevice` for an unknown type.
- Subclasses are registered with the `@register_device("type", "alias", …)`
  decorator. Registering an already-registered type name raises at import time
  (catches duplicate-alias bugs early).
- Currently subclasses mostly customise the display `category`; the base
  `Device.send_command()` is a `NotImplementedError` placeholder — control I/O
  is config-driven via `controls.py`/`network.py` rather than per-class code.

### Forward compatibility via `extra`

Any JSON key a model doesn't explicitly know is preserved in an `extra` dict
(on both `Device` and `Room`) and written back verbatim by `to_dict()`. This is
how `web_url`, `web_*`, `commands`, and the recorder URLs survive a
load → edit → save round-trip even though the dataclasses don't declare them as
first-class fields. It also means a newer config feature won't be silently
dropped by an older build.

### Runtime state vs. config

A `Device` carries non-config runtime fields (`status`, `last_latency_ms`,
`last_error`, and for recorders `recording_status`) marked `compare=False` so
they don't affect equality or serialise back to disk. The status checker mutates
these; the UI reads them.

### Useful model methods

- `Room.devices_by_category()` — grouping used to lay out the cards.
- `Room.web_urls()` — de-duplicated web URLs for **Open Web UIs**.
- `Room.room_health` — aggregate status (all online → ONLINE, all offline →
  OFFLINE, mixed/in-progress → amber, none checked → UNKNOWN).
- `Site.grouped_by_city()` / `is_multi_city` — drives the sidebar's city boxes
  vs. flat list.
- `Site.to_dict()` / `Room.to_dict()` / `Device.to_dict()` — the inverse of
  `from_dict`, used when the in-app editors save.

---

## Threading model

Tkinter is single-threaded; the UI must only ever be touched from the Tk
("main") thread. mission-deck respects this strictly:

- **UI thread** — runs Tk exclusively. All widget creation and mutation happens
  here.
- **Status-check worker** — `network.run_status_checks()` runs on a daemon
  thread. It spins up its own `asyncio` event loop, probes all of a room's
  devices **concurrently**, and publishes each `CheckResult` to a thread-safe
  queue as it completes.
- **Control worker** — device commands (HTTP/TCP) and recording polls run on a
  daemon thread via `app.run_background(work, on_done)`.
- **Result marshalling** — `app.after(...)` polls the result queue on the Tk
  timer and applies updates on the UI thread, so cards flip green/red live
  without ever blocking the interface.

A **generation counter** guards stale results: when you switch rooms or start a
new check, results from an older check are discarded rather than scribbled onto
the wrong room.

> Rule of thumb when editing: anything that does network or file I/O belongs off
> the Tk thread; anything that touches a widget must be back on it (via
> `after`). See [Networking & Device Control](Networking-and-Device-Control.md).

---

## Startup & config-discovery flow

`app.main()` (the entry point) does, in order:

1. `setup_logging()` — configure the diagnostic + audit log streams and install
   crash-capturing excepthooks (see [Logging & Auditing](Logging-and-Auditing.md)).
2. Emit the `app.start` audit event.
3. `AppState.load()` — read per-user preferences/recents.
4. `_run(state)` — the **startup / soft-restart loop**:
   - Decide what to load (`_resolve_startup_choice`):
     1. the remembered `last_config_path`, if it still exists;
     2. else a config discovered in the standard locations;
     3. else show the **WelcomeWindow** and use its result (file / demo / quit).
   - Load and type it (`load_config` → `Site.from_loaded_config`). On failure,
     show an error dialog, audit `config.load ok=false`, clear the remembered
     path, and loop back to the welcome screen rather than spinning on a bad
     file.
   - Audit a successful `config.load`, remember the path, and run the `App`.
   - When the app closes: if the user requested a **config switch**, loop with
     the new path (a clean soft-restart); otherwise exit.
5. A top-level `try/except` wraps everything as a last-resort safety net: a fatal
   error is logged with a full traceback and shown in a dialog before re-raising.

This loop is why **switching config** in Settings works seamlessly — it tears
down the `App` and re-enters discovery with the chosen file.

---

## UI composition (`app.py`)

Key widget classes:

| Class | Role |
|-------|------|
| `App(ctk.CTk)` | The main window; owns the sidebar, main panel, status bar, the result queue, and all orchestration. |
| `RoomButton` | A sidebar room entry with a health dot. **Pooled** and rebound. |
| `CityGroup` | A collapsible city box; builds its room buttons **lazily** on first expand. |
| `DeviceCard` | A device tile (name, description, address, status). **Pooled** and rebound via `set_device()`. |
| `DeviceControlDialog` | Per-device control panel: Open Web UI, command buttons, recorder controls, edit affordances. |
| `SettingsDialog` | Appearance, timeout, auto-refresh, browser, switch-config. |
| `WelcomeWindow` | First-run chooser (open file / recent / demo). |
| `editors.py` dialogs | `RoomEditorDialog`, `DeviceEditorDialog`, `CommandEditorDialog`. |

### Widget pooling

`DeviceCard` and `RoomButton` widgets are **pooled** and rebound rather than
recreated on every navigation, and `CityGroup` builds its buttons lazily on
first expand. This keeps room switching snappy even at 100+ rooms — the
performance target the project explicitly designs for.

---

## Where to look for a given concern

| I want to change… | Look in |
|-------------------|---------|
| The config schema or validation | `config.py` (structure) + `models.py` (content) |
| A device type or its category | `models.py` (`@register_device`) |
| How status checks probe devices | `network.py` |
| How a control command is built/run | `controls.py` + `network.py` |
| How web UIs open | `browser.py` |
| Colours / sizing | `theme.py` |
| Logging or the audit trail | `logging_setup.py` |
| Anything visual | `app.py` / `editors.py` |
