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
├── __init__.py        # version / app metadata (__version__ = "0.2.0")
├── __main__.py        # `python -m mission_deck` → app.main()
├── config.py          # locate / load / structurally validate JSON; fetch+cache remote config
├── models.py          # Site → Room → Device typed models + registry
├── network.py         # async status probes (monitor registry) + HTTP/TCP control transports
├── controls.py        # build per-device control action lists
├── browser.py         # open URLs in the configured browser
├── history.py         # persisted uptime-history store (SQLite)
├── dashboard.py       # estate-wide Overview view (flat status report)
├── dashboards.py      # composable, user-ordered dashboard boards (widget catalogue)
├── report.py          # estate status report export (CSV)
├── state.py           # persisted per-user preferences + recent files
├── theme.py           # colour palette + sizing tokens
├── palette.py         # global command palette (Ctrl+K) fuzzy jump
├── toast.py           # non-blocking toast notifications
├── ui.py              # cached fonts, button/switch style tokens, PromptDialog
├── icons.py           # vector icon factory (CTkImage)
├── plugins.py         # Plugins screen + plugin catalogue (PluginSpec)
├── cloud.py           # Cloud Sync view (HTTPS config source, plugin-gated)
├── activity.py        # Activity Log view (browsable audit trail, plugin-gated)
├── logging_setup.py   # diagnostic + audit logging
├── editors.py         # in-app room/device/command editor dialogs
└── app.py             # CustomTkinter UI + event orchestration
```

### Responsibilities and constraints

| Module | Job | Must NOT |
|--------|-----|----------|
| `config.py` | Find a config file, parse JSON, validate top-level shape; atomic save; fetch+cache a remote config (`fetch_remote_config` → `cloud-config.json`, stdlib `urllib`). | Touch the GUI or device internals. |
| `models.py` | Turn raw config into typed `Site`/`Room`/`Device` objects; device registry. | Do I/O, GUI, or networking. Stays logging-free (purity). |
| `network.py` | Async reachability probes via a pluggable **monitor registry** (`tcp`/`http`/`https`/`ping`/`tls`), bounded by a concurrency semaphore; blocking HTTP/TCP control transports; recording-status fetch. | Touch the GUI. Must be thread-safe; results via callback. |
| `controls.py` | Build `DeviceControl` action lists from config `commands` + built-in "Open Web UI". | Do the actual I/O itself (delegates to `network.py`). |
| `browser.py` | Launch URLs via subprocess/`webbrowser`. | Import models or touch the GUI. |
| `history.py` | Best-effort SQLite store of reachability samples; per-device/per-room uptime/latency queries. | Anything beyond stdlib `sqlite3`; raising into callers. |
| `dashboard.py` | Render the estate-wide Overview (flat report) from live `Site` + `HistoryStore`. | Networking; touching worker threads. Presentation only, UI thread. |
| `dashboards.py` | Render the user-composed **Dashboards** boards from a widget catalogue persisted in `AppState`. | Networking; same constraints as `dashboard.py`. Presentation only, UI thread. |
| `report.py` | Build the estate status report (per-device status/latency/24h uptime) for CSV export. | GUI or networking; the UI picks the path and runs it off the Tk thread. |
| `state.py` | Load/save per-user `state.json` (best-effort). | Break the app if the file is missing/corrupt. |
| `theme.py` | Pure colour/size constants. | Contain logic. Stays logging-free. |
| `palette.py` | Build & show the global command palette (Ctrl+K); fuzzy jump to rooms/devices/actions. | I/O. Items built from live `Site`; every action is a callback into `App`. |
| `toast.py` | Non-blocking bottom-right toast stack (capped, auto-dismiss). | I/O. Callers pass finished strings. |
| `ui.py` | Cached shared fonts, button/switch style tokens, `PromptDialog`. | Logic beyond presentation; cache resets per Tk root. |
| `icons.py` | Vector icon factory (`CTkImage`). | Anything beyond presentation. |
| `plugins.py` | The **Plugins** screen + the static plugin catalogue (`PluginSpec`). | Hold activation state (that lives in `AppState`). |
| `cloud.py` | The **Cloud Sync** view (HTTPS config source, plugin-gated). | Do the download itself — delegates to `config.fetch_remote_config` off-thread. |
| `activity.py` | The **Activity Log** view (browsable audit trail, plugin-gated). | Networking or writing; reads `logging_setup.tail_audit` (read-only). |
| `logging_setup.py` | Configure diagnostic + audit logging; install excepthooks. | Use anything beyond stdlib; raise into callers. |
| `editors.py` | Tkinter dialogs for editing rooms/devices/commands. | Bypass `models`/`config` validation. |
| `app.py` | The CustomTkinter UI; marshals worker results onto the Tk thread. | Hold business rules that belong in `models`. |

The dependency direction is roughly:

```
app.py ── editors.py
  │  ├── controls.py ── network.py ── models.py ── config.py
  │  ├── browser.py
  │  ├── history.py ────────────────── models.py ── config.py
  │  ├── dashboard.py ── history.py + models.py + logging_setup.py
  │  ├── dashboards.py ── history.py + models.py + logging_setup.py
  │  ├── report.py ────── history.py + models.py
  │  ├── palette.py ───── models.py
  │  ├── plugins.py ───── state.py + icons.py + theme.py + ui.py
  │  ├── cloud.py ─────── config.py (fetch_remote_config)
  │  ├── activity.py ──── logging_setup.py (tail_audit)
  │  ├── toast.py / icons.py / ui.py ── theme.py
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
  thread. It spins up its own `asyncio` event loop, probes devices
  **concurrently** through each device's registered monitor, and publishes each
  `CheckResult` to a thread-safe queue as it completes. Concurrency is **bounded**
  by an `asyncio.Semaphore` (default `network.DEFAULT_MAX_CONCURRENCY = 128`,
  tunable via `max_concurrent_checks`) so an estate-wide sweep of thousands of
  devices never opens thousands of sockets at once. The same driver powers both
  the per-room check and the estate-wide sweep (separate queues/generations).
- **Control worker** — device commands (HTTP/TCP) and recording polls run on a
  daemon thread via `app.run_background(work, on_done)`.
- **History writes** — each completed check/sweep appends a batch of
  `history.Sample`s, persisted off the UI thread via `run_background` (SQLite,
  best-effort).
- **Result marshalling** — `app.after(...)` polls the result queue on the Tk
  timer and applies updates **in a batch, once per tick** (not per device), so
  cards flip green/red live without ever blocking the interface.

A **generation counter** guards stale results: when you switch rooms or start a
new check, results from an older check are discarded rather than scribbled onto
the wrong room. The estate sweep uses its own queue + generation counter.

> Rule of thumb when editing: anything that does network or file I/O belongs off
> the Tk thread; anything that touches a widget must be back on it (via
> `after`). See [Networking & Device Control](Networking-and-Device-Control.md).

---

## The Overview dashboard & estate-wide sweep

The **Overview** (`dashboard.py`) is a top-level view, swapped with the room
panel in the same grid cell (`App.show_dashboard()` / `show_room_view()`;
"OVERVIEW" tab in the top nav bar). It is deliberately **not** a bento grid of
boxed cards: it renders as a flat, single-column report — a bare stat strip
(rooms / devices / online / offline / healthy), then hairline-ruled sections for
attention (offline devices), recorders, per-room 24h uptime and a
recent-activity feed (`logging_setup.tail_audit`). It is **pure presentation**:
it reads live `Site` state plus the `HistoryStore` and calls back into `App`
(refresh, toggle polling, jump to a room); it never touches the network itself.
The destroy-and-rebuild list sections are guarded by **content signatures** and
capped at `MAX_LIST_ROWS`, so a refresh whose data hasn't changed (or an estate
with hundreds of offline devices) doesn't rebuild thousands of widgets. The
overview topbar also offers **EXPORT** — a CSV status report via `report.py`
(written off the Tk thread; audited as `report.export`).

The **Dashboards** tab (`dashboards.py`) is the user-composed counterpart: a
board of widgets (KPI tiles, 24h uptime/latency trend bars via
`history.uptime_buckets`/`latency_buckets`, offline/recorder/room-uptime/activity
lists) added from a catalogue dialog, reordered/removed in place, and persisted
as an ordered id list in `AppState.dashboard_widgets` (`None` = default starter
board). It refreshes when a sweep/check finishes while it is the active view, and
on navigate (throttled), mirroring the overview.

`App.run_estate_sweep()` probes **all** rooms (`site.all_devices()`) using the
same worker/queue/`after()` pattern as a room check but on its own queue +
generation counter. A background timer
(`dashboard_poll_enabled`/`dashboard_poll_seconds` in `AppState`) can re-run the
sweep on an interval. Whether the app opens on the Overview or a room is the
`start_on_dashboard` preference.

## Uptime history

`history.HistoryStore` is a best-effort SQLite store at
`<user_config_dir>/history.db` (override with `MISSION_DECK_HISTORY_DB`). One
shared connection (`check_same_thread=False` + a lock, WAL mode) serves Tk-thread
reads and worker-thread writes. Each completed check/sweep appends a batch of
`Sample`s; only resolved (ONLINE/OFFLINE) samples are stored. `uptime` /
`room_uptime` / `rooms_uptime` return `None` when a window has no data (so the UI
shows "—", not a false 0%). Samples older than `history_retention_days` are
pruned on open. Like `state.py` and the audit log, it **never raises into
callers**.

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

The window stacks top to bottom: a **top nav bar** (brand mark, labelled view
tabs with an accent active-underline, a live "N OFFLINE" attention badge, the
Ctrl+K search button, settings, and the operator chip), a slim **context bar**
(breadcrumb + per-view actions, swapped on navigate), then the active view.
There is no left icon rail — top-level navigation is horizontal. The Rooms view
keeps its own **room sidebar** (collapsible city boxes). Plugin-contributed views
(Cloud Sync, Activity) add/remove their nav tab at runtime via
`App._add_nav_tab` / `_remove_nav_tab`.

Key widget classes:

| Class | Role |
|-------|------|
| `App(ctk.CTk)` | The main window; owns the top nav bar, context bar, the swappable views container, the result queue, and all orchestration. |
| `NavTab` | One labelled tab in the top nav bar with an accent active-underline. |
| `DashboardView` | The estate-wide **Overview** (`dashboard.py`): flat report — stat strip + attention/recorders/uptime/activity sections. Built once, repainted by `refresh()`. |
| `DashboardsView` | The user-composed **Dashboards** board (`dashboards.py`): widgets from a catalogue, reordered/removed, persisted in `AppState`. |
| `PluginsView` | The **Plugins** screen (`plugins.py`): the plugin catalogue with enable/disable toggles. |
| `CloudSyncView` / `ActivityView` | Plugin-gated views (`cloud.py` / `activity.py`) whose nav tab appears only while the plugin is enabled. |
| `RoomButton` | A Rooms-view sidebar entry with a health dot. **Pooled** and rebound. |
| `CityGroup` | A collapsible city box; builds its room buttons **lazily** on first expand. |
| `DeviceCard` | A device tile (name, description, address, status). **Pooled** and rebound via `set_device()`. |
| `DeviceControlDialog` | Per-device control panel: Open Web UI, command buttons, recorder controls, edit affordances. |
| `SettingsDialog` | Appearance, timeout, auto-refresh, browser, concurrency, switch-config. |
| `CommandPalette` / `ShortcutsDialog` | Ctrl+K fuzzy jump (`palette.py`) and the F1 shortcut reference. |
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
| A new reachability check type | `network.py` (`@register_monitor`) |
| The Overview / estate-wide health | `dashboard.py` + `app.run_estate_sweep` |
| User-composed dashboard boards | `dashboards.py` (`WidgetSpec` / `WIDGETS`) |
| The CSV status report | `report.py` |
| Uptime / latency history / storage | `history.py` |
| How a control command is built/run | `controls.py` + `network.py` |
| How web UIs open | `browser.py` |
| The Plugins screen / catalogue | `plugins.py` (`PluginSpec` / `PLUGINS`) |
| Cloud Sync (remote config source) | `cloud.py` + `config.fetch_remote_config` |
| The Activity Log view | `activity.py` |
| The command palette (Ctrl+K) | `palette.py` |
| Toast notifications | `toast.py` |
| Icons | `icons.py` |
| Shared fonts / button tokens / prompts | `ui.py` |
| Colours / sizing | `theme.py` |
| Logging or the audit trail | `logging_setup.py` |
| Anything visual | `app.py` / `editors.py` |
