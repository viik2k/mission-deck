# Glossary

Domain, AV, and mission-deck-specific terms used throughout this wiki.

---

### AV-over-IP
Distributing audio/video as network streams instead of over dedicated HDMI/SDI
cabling. In mission-deck this maps to **Video Matrix**, **Video Encoder (TX)**,
and **Video Decoder (RX)** device types (e.g. Blustream, Crestron NVX).

### Audit log
The append-only, JSON-per-line record of operator actions (`audit.log`). One
line per event with `ts`, `event`, `user`, and event-specific fields. See
[Logging & Auditing](Logging-and-Auditing.md).

### Bounded concurrency
A cap (`DEFAULT_MAX_CONCURRENCY = 128`, tunable via `max_concurrent_checks`) on
how many status probes run at once, enforced by an `asyncio.Semaphore`. Lets an
estate-wide sweep of thousands of devices run as a steady stream rather than
opening thousands of sockets at once.

### City box / City group
A collapsible grouping of rooms in the sidebar, keyed by a room's `city` field.
Keeps large multi-site estates navigable. Rendered by the `CityGroup` widget.

### Control processor
The automation "brain" of an AV room (e.g. a Crestron CP4) that orchestrates
displays, cameras, audio, etc. Device type `control_processor` (aliases
`crestron`, `controller`).

### Control transport
The mechanism that delivers a control command to a device — `http_request`
(HTTP/HTTPS) or `tcp_send` (raw TCP) in `network.py`.

### DeviceControl
The in-app representation of one control button: an `id`, `label`, a `run(value)`
callable, an optional `prompt`, and a `kind` (`command` or `web`). Built by
`controls.py`.

### DeviceStatus
The reachability state shown by a card's indicator: `UNKNOWN` (grey),
`CHECKING` (amber), `ONLINE` (green), `OFFLINE` (red).

### Diagnostic log
The human-readable support/debug log (`mission-deck.log`). Distinct from the
audit log. See [Logging & Auditing](Logging-and-Auditing.md).

### Estate sweep
A status check that probes **every device in every room** at once
(`App.run_estate_sweep`), powering the Overview's "Refresh All" and its optional
background poll. Uses its own queue + generation counter, separate from a
per-room check. Emits a `status_check.estate` audit event.

### DSP (Audio DSP)
Digital Signal Processor — the audio mixing/routing appliance in a room (e.g.
Biamp Tesira, QSC Core). Device type `audio_dsp` (alias `dsp`).

### `extra`
The catch-all dict on `Device`/`Room` that preserves any JSON key the model
doesn't explicitly declare, so forward-compatible fields (`web_*`, `commands`,
recorder URLs, …) survive a load → edit → save round-trip.

### Generation counter
A monotonically increasing stamp on each status-check run. Results whose
generation doesn't match the current run/room are discarded, preventing stale
probes from updating the wrong cards.

### HistoryStore / Uptime history
The best-effort SQLite store (`history.py`, `history.db`) of device reachability
samples over time. One `Sample` per resolved device is appended after each
check/sweep; it answers per-device and per-room **uptime percentage** queries
that the Overview's uptime panel renders. Never raises into callers; override the
DB path with `MISSION_DECK_HISTORY_DB`.

### Monitor / Monitor registry
A *monitor* decides how a device's reachability is judged — an
`async (Device, float) -> CheckResult`. `network.py` holds a registry
(`@register_monitor`, mirroring `@register_device`): `tcp` (default, TCP connect),
`http`/`https` (any answered endpoint = up), `ping`/`icmp` (one ICMP echo) and
`tls`/`ssl` (a completed TLS handshake on the HTTPS port). A device opts in with a
`"monitor"` config key. Adding a check type is one decorator.

### Overview / Dashboard
The estate-wide homepage (`dashboard.py`, `DashboardView`): a flat, single-column
report — a stat strip, a needs-attention list, an estate recorders panel,
per-room 24h uptime, and a recent-activity feed. Pure presentation — it reads
live `Site` state + the `HistoryStore` and never touches the network itself.
Reached via the "Overview" tab in the top nav bar.

### Dashboards (composable)
The user-composed counterpart to the Overview (`dashboards.py`): a board of
widgets (KPI tiles, 24h uptime/latency trend bars, offline/recorder/room-uptime/
activity lists) added from a catalogue, reordered/removed in place, and persisted
per-operator in `AppState.dashboard_widgets`.

### Plugins
The Plugins screen (`plugins.py`, `PluginSpec`/`PLUGINS`): a catalogue of the
built-in monitors (always on) plus optional, toggleable plugins. Enabling a
plugin that contributes a view (Cloud Sync, Activity Log) adds its nav tab;
activation state lives in `AppState.enabled_plugins`.

### Cloud Sync
A plugin-gated view (`cloud.py`) that pulls `config.json` from a central HTTPS
URL via `config.fetch_remote_config` — validated, cached locally, audited
(`cloud.sync`), and loaded via a soft restart.

### Activity Log
A plugin-gated view (`activity.py`) that reads the local `audit.log` via
`logging_setup.tail_audit` and presents a full-screen, filterable browser of
operator actions. Read-only; nothing leaves the workstation.

### Command palette
The Ctrl+K (or Ctrl+P) global launcher (`palette.py`): fuzzy-jump to any room,
device, or global action. Items are built from the live `Site` each time it
opens; every action is a callback into `App`.

### Toast notification
A non-blocking, auto-dismissing message in a bottom-right stack (`toast.py`) —
used for sweep results, newly-offline devices, report-exported and
settings-saved confirmations.

### Open Web UIs
The headline feature: open the management web page of every web-accessible
device in the current room at once, in the configured browser.

### Ping timeout
`ping_timeout_seconds` — how long a status probe waits for a TCP connection
before declaring a device offline. Default `2.0`. (It's a TCP-connect probe, not
an ICMP ping.)

### PTZ camera
Pan-Tilt-Zoom camera. Device type `ptz_camera` (alias `camera`).

### RecordingStatus
A `recorder`'s recording state: `UNKNOWN`, `IDLE`, `RECORDING`, `PAUSED`. Polled
separately from reachability via `recording_status_url`.

### Registry (device registry)
The `type → class` map in `models.py`. Subclasses register with
`@register_device(...)`; `Device.from_dict()` dispatches on the `type` field.

### Schema version
The integer `schema_version` declared at the top of every config. This build
supports version **`1`**; an unsupported value is rejected with a clear error.

### Site
The top-level model object: all loaded rooms plus the `app` settings block.

### Soft restart
The mechanism behind "switch config": the running `App` is torn down and the
startup loop (`_run`) re-enters config discovery with the chosen file, loading it
cleanly.

### State (`state.json`)
Per-user preferences and recent-files, stored in the per-user config directory.
Best-effort: a corrupt/missing file falls back to defaults. Overrides matching
`app` config values.

### TX / RX
Transmit (encoder) and Receive (decoder) endpoints in an AV-over-IP system.
Device types `video_encoder` / `video_decoder`.

### VC codec
Video-conferencing codec/endpoint (Cisco Webex, Poly, Lifesize, Zoom Room).
Device type `vc_codec` and aliases. Driven via authenticated HTTP commands
(Cisco xCommand, Poly REST).

### Web URL resolution
The logic in `Device.web_url` that decides a device's browser-openable address
from `web_url` / `web_protocol` / `web_port` / `web_path`, falling back to the
control `protocol`/`port`. Devices with no web scheme yield `None` and are
skipped by Open Web UIs.

### Widget pooling
The performance technique of reusing `DeviceCard`/`RoomButton` widgets (rebinding
them to new data) instead of recreating them on every navigation.
