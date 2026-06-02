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
