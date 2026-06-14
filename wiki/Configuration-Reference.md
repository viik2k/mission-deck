# Configuration Reference

mission-deck loads everything it displays — rooms, devices, control buttons,
browser preferences — from an external `config.json`. This page is the complete
reference for that file.

> 🔐 **`config.json` is git-ignored and contains your real infrastructure.** The
> repository ships only `config.example.json` with dummy data. Never commit real
> IPs, hostnames, or device names. See the [Security Model](Security-Model.md).

---

## Config discovery order

At startup mission-deck searches these locations and loads the **first one that
exists**:

1. The path in the **`MISSION_DECK_CONFIG`** environment variable, if set.
2. **`config.json` in the current working directory.**
3. **`config.json` next to the application** (the repo root, or the folder
   holding the EXE when packaged).
4. The **per-user config directory**:
   - **Windows:** `%APPDATA%\mission-deck\` (falls back to
     `~\AppData\Roaming`)
   - **macOS:** `~/Library/Application Support/mission-deck/`
   - **Linux:** `$XDG_CONFIG_HOME/mission-deck/` (falls back to
     `~/.config/mission-deck/`)

Paths are de-duplicated while preserving priority order. If **no** config is
found, the [welcome screen](User-Guide.md#the-welcome-screen) is shown. The last
config you open is remembered (in `state.json`) and re-opened next launch without
prompting.

---

## Top-level structure

```jsonc
{
  "schema_version": 1,       // required — must be 1 for this build
  "app": { /* app settings, optional */ },
  "rooms": [ /* one or more rooms, required */ ]
}
```

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `schema_version` | integer | **yes** | Must equal `1`. A missing, non-integer, or unsupported value is rejected with a clear error. |
| `app` | object | no | Application-wide settings (see below). |
| `rooms` | array | **yes** | Must be a list; each element must be an object. |

### Validation in two stages

1. **Structural validation** (`config.py`) is cheap and checks only the shape:
   top-level is an object, `schema_version` matches, `rooms` is a list of
   objects, each room's `devices` (if present) is a list. Failures raise
   `ConfigValidationError`.
2. **Content validation** (`models.py`) types each room and device and raises
   `DeviceConfigError` for problems like a missing `host` or a duplicate id.

Both produce **human-readable** messages surfaced in a dialog — never a raw
stack trace in the UI.

---

## App settings (`app`)

Optional global knobs:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `ping_timeout_seconds` | number | `2.0` | Seconds to wait for a device probe during a status check before marking it offline. |
| `max_concurrent_checks` | integer | `0` (→ built-in `128`) | Cap on how many status probes run at once. Bounds an estate-wide sweep so it never opens thousands of sockets at once. A per-user Settings value overrides this. |
| `default_appearance` | string | — | Preferred theme hint (`"dark"`, `"light"`, `"system"`). |
| `browser` | string or object | — | How **Open Web UIs** launches the browser (see [Browser configuration](#browser-configuration)). |

```jsonc
"app": {
  "ping_timeout_seconds": 2.0,
  "max_concurrent_checks": 128,
  "default_appearance": "dark",
  "browser": { "path": "", "name": "", "new_window": true }
}
```

> Per-user **Settings** dialog values (timeout, auto-refresh, browser,
> appearance) are stored separately in `state.json` and **override** the `app`
> block at runtime. See [Per-user state](#per-user-state).

---

## Rooms

Each object in `rooms` is a physical space (a courtroom).

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `id` | string | **yes** | Unique across all rooms (e.g. `"courtroom-1a"`). Duplicates are rejected. |
| `name` | string | **yes** | Display label (e.g. `"Courtroom 1A"`). |
| `city` | string | no | Groups the room into a collapsible **city box** in the sidebar. Omit and the room falls under `Ungrouped`; if *no* room has a city, the sidebar shows a flat list. |
| `location` | string | no | Physical location (e.g. `"1st Floor – East Wing"`). Also searchable. |
| `notes` | string | no | Free-text notes. |
| `devices` | array | no | Devices in this room (see below). Must be a list; device ids must be unique within the room. |

Unknown room-level keys are preserved across a load → edit → save round-trip
(stored in the room's `extra`).

---

## Devices

Each object in a room's `devices` array is one piece of AV equipment.

### Required fields

| Key | Type | Description |
|-----|------|-------------|
| `id` | string | Unique within its room (e.g. `"1a-crestron-cp4"`). |
| `name` | string | Display name. |
| `type` | string | A registered type (see [Device types](#device-types)); unknown values load as a Generic Device. |
| `host` | string | IP address or hostname. |

All four must be non-empty strings, or the device is rejected with a
`DeviceConfigError`.

### Optional fields

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `port` | integer `0–65535` | per-protocol default | Port used for **status checks**. Defaults: `http`→80, `https`→443, `ssh`→22, `telnet`→23, `tcp`→0. A raw-`tcp` device with no port can't be status-checked (shows Unknown). |
| `protocol` | string | `"tcp"` | Control/status transport: `tcp`, `http`, `https`, `ssh`, `telnet`. |
| `manufacturer` | string | `""` | Shown on the card. |
| `model` | string | `""` | Shown on the card. |
| `tags` | array of strings | `[]` | Free-form tags for grouping/filtering. Must be a list. |
| `web_url` | string | — | Explicit web-UI URL; overrides all web-derivation below. |
| `web_protocol` | string | — | `http`/`https` for the web UI when it differs from the control protocol. |
| `web_port` | integer | — | Web-UI port when it differs from the control port. |
| `web_path` | string | — | Path appended to the web-UI URL (e.g. `/admin`). |
| `monitor` | string | `tcp` | How reachability is judged — `tcp`, `http`, or `https` (see [Monitors](#monitors)). |
| `health_url` | string | — | URL the `http`/`https` monitor probes (else the device's `web_url`). |
| `commands` | array | — | Control action buttons (see [Control commands](#control-commands)). |

Any **unknown keys** are preserved in the device's `extra` map for
forward-compatibility, and written back verbatim on save. (`web_*`, `commands`,
and the recorder URLs below all live there.)

### Control port vs. web UI — an important distinction

A device's **status check** uses `protocol`/`port`. **Open Web UIs** uses a
separately-resolved web URL. They are intentionally decoupled: a Crestron
processor may be *controlled* on TCP/41794 but expose its admin page on HTTP/80.

**Web-URL resolution order** (`Device.web_url`):

1. An explicit `web_url` field (used verbatim).
2. Otherwise a scheme from `web_protocol` if it's `http`/`https`; else the
   device's `protocol` if *that* is a web scheme; else **no web URL** (the
   device is skipped by Open Web UIs).
3. Port: `web_port` if given, else `port`. The port is omitted from the URL when
   it equals the scheme default (80/443).
4. `web_path` is appended (a leading `/` is added if missing).

So an SSH-only DSP or a raw-TCP display yields no web URL and is silently
skipped — only devices with a real web interface are opened.

```jsonc
{
  "id": "1a-crestron-cp4",
  "name": "Crestron Control Processor",
  "type": "control_processor",
  "manufacturer": "Crestron", "model": "CP4",
  "host": "10.10.1.10",
  "port": 41794, "protocol": "tcp",   // status check → TCP/41794
  "web_protocol": "http", "web_port": 80,  // web UI → http://10.10.1.10
  "tags": ["control", "automation"]
}
```

---

## Device types

The `type` field is **case-insensitive** and maps to a device class. Each class
currently customises the display **category** (the card grouping); unknown types
fall back to **Generic Device** so an unrecognised type never breaks loading.

| `type` aliases | Category shown |
|----------------|----------------|
| `ptz_camera`, `camera` | PTZ Camera |
| `control_processor`, `crestron`, `controller` | Control Processor |
| `audio_dsp`, `dsp` | Audio DSP |
| `display`, `monitor`, `tv` | Display |
| `document_camera`, `doc_camera` | Document Camera |
| `recorder` | Recorder (supports recording controls — see below) |
| `video_matrix`, `matrix_switcher`, `av_matrix`, `blustream`, `nvx`, `extron_matrix`, `amx_matrix`, `atlona`, `wyrestorm` | Video Matrix |
| `video_encoder`, `av_encoder`, `encoder`, `tx`, `blustream_tx`, `nvx_tx` | Video Encoder (TX) |
| `video_decoder`, `av_decoder`, `decoder`, `rx`, `blustream_rx`, `nvx_rx` | Video Decoder (RX) |
| `vc_codec`, `video_conference`, `codec`, `cisco_webex`, `webex`, `poly`, `lifesize`, `zoom_room` | Video Conferencing |
| *(anything else)* | Generic Device |

Adding a new type is a one-line code change — see the
[Developer Guide](Developer-Guide.md#adding-a-new-device-type).

---

## Monitors

How a device's reachability is judged is **pluggable**. A device picks a monitor
with the `"monitor"` key; absent it, the default `tcp` monitor is used (so every
existing config behaves unchanged).

| `monitor` | What it does |
|-----------|--------------|
| `tcp` *(default)* | Opens a TCP connection to `host:port`. Online = the port accepts a connection. |
| `http` / `https` | Issues an HTTP(S) request; **any answered endpoint** (even a 4xx/5xx) is treated as up — only a transport failure is offline. The URL is taken from `health_url`, else the device's resolved `web_url`; it falls back to a `tcp` probe if neither resolves. Honours `verify_tls: false` for self-signed-cert appliances. |
| `ping` / `icmp` | Sends one ICMP echo via the system `ping` binary — for devices with no open TCP port. On Windows a reply only counts as up when it carries a TTL. |
| `tls` / `ssl` | Completes a TLS handshake on the HTTPS port — a stronger "alive and speaking TLS" signal than an open socket for web-managed gear. The port is taken from `tls_port`, else the device's `port`, else `443`. Certificates are **not** verified by default; set `verify_tls: true` to make an untrusted/expired/wrong-host certificate count as offline. |

```jsonc
{
  "id": "1a-recorder", "name": "Digital Court Recorder", "type": "recorder",
  "host": "10.10.1.50", "protocol": "https", "web_path": "/admin",
  "monitor": "https",
  "health_url": "https://10.10.1.50/api/health"   // optional; else web_url is probed
}
```

Adding a new monitor type is a one-decorator code change — see the
[Developer Guide](Developer-Guide.md#3a-adding-a-new-monitor-status-check-type).

---

## Recorder fields

A `recorder` device supports live recording status and start/stop controls via
these optional keys (stored in `extra`):

| Key | Description |
|-----|-------------|
| `recording_status_url` | URL polled (GET) to read recording state. |
| `recording_status_json_path` | Dot-path into the JSON response, e.g. `"state.recording"`. Empty = use the whole response body. |
| `recording_start_url` | URL invoked to start recording. |
| `recording_stop_url` | URL invoked to stop recording. |

The status value is interpreted leniently: booleans, `0/1`, and strings like
`recording`/`active`/`on`/`running` → **Recording**; `paused`/`suspended` →
**Paused**; `idle`/`stopped`/`off`/`ready` → **Idle**; anything else → Unknown.
Any network or parse error yields **Unknown** (never an error to the operator).

```jsonc
{
  "id": "1a-recorder", "name": "Digital Court Recorder",
  "type": "recorder", "manufacturer": "JAVS", "model": "Notewise",
  "host": "10.10.1.50", "port": 443, "protocol": "https",
  "web_path": "/admin",
  "recording_status_url": "https://10.10.1.50/api/recording/status",
  "recording_status_json_path": "state.recording",
  "recording_start_url": "https://10.10.1.50/api/recording/start",
  "recording_stop_url": "https://10.10.1.50/api/recording/stop"
}
```

---

## Control commands

Every entry in a device's `commands` array becomes a **button** in the device's
control panel — **no code required**. This is what keeps mission-deck
vendor-neutral: instead of hard-coding a Crestron/Sony/Cisco API, you describe
the actual HTTP request or TCP payload.

### Common keys

| Key | Applies to | Description |
|-----|------------|-------------|
| `id` | all | Identifier (falls back to `label`, then `"command"`). |
| `label` | all | Button text. **Required** in practice — the editor insists on it. |
| `protocol` | all | `http`, `https`, or `tcp` (default `tcp`). |
| `prompt` | all | When set, the UI asks the operator for a value before sending; the answer fills `{value}`. |
| `timeout` | all | Seconds before the command gives up (default `5.0`). |

### Placeholders

`{host}`, `{port}`, and `{value}` are substituted at runtime in the `url`,
`payload`, and `body`:

- `{host}` → the device's `host`
- `{port}` → the device's `port`
- `{value}` → the prompted value (empty string if no prompt)

### HTTP / HTTPS commands

| Key | Description |
|-----|-------------|
| `url` | **Required.** Request URL (placeholders substituted). |
| `method` | `GET` (default), `POST`, etc. |
| `body` | Request body (placeholders substituted), sent UTF-8. |
| `headers` | Object of extra request headers. |
| `auth` | `{"username": …, "password": …}` (or `[user, pass]`) → an `Authorization: Basic` header. |
| `verify_tls` | `false` to skip certificate verification (courtroom codecs often present self-signed certs on the LAN). Default `true`. |

A command "reaching" the device — even with a non-2xx HTTP status — is reported
as a result, not an error; only connection failures raise an error.

### TCP commands

| Key | Description |
|-----|-------------|
| `payload` | **Required.** Bytes to send (placeholders substituted, UTF-8). Use `\r`, `\r\n` as the device expects. |
| `port` | Override the device's control port for this command. |
| `read_response` | `true` to wait briefly for and display a short reply. |

### Examples

```jsonc
"commands": [
  { "id": "preset1", "label": "Camera Preset 1", "protocol": "http",
    "url": "http://{host}/cgi-bin/ptzctrl?action=recall&preset=1" },

  { "id": "power_on", "label": "Power On", "protocol": "tcp",
    "payload": "PWR ON\r", "port": 4998 },

  { "id": "send", "label": "Send Command", "protocol": "tcp",
    "payload": "{value}\r", "prompt": "Command to send", "read_response": true },

  { "id": "dial", "label": "Place Call…", "protocol": "https", "method": "POST",
    "url": "https://{host}/putxml",
    "body": "<Command><Dial><Number>{value}</Number></Dial></Command>",
    "prompt": "Address or number to dial",
    "auth": {"username": "admin", "password": "•••"}, "verify_tls": false }
]
```

> 🔐 **Codec credentials** in `auth` are real secrets. They belong only in your
> git-ignored `config.json`, **never** in `config.example.json`. See the
> [Security Model](Security-Model.md#credentials-in-config).

---

## Browser configuration

The `app.browser` setting controls **Open Web UIs**. It accepts a string or an
object:

```jsonc
// As an object (most explicit):
"browser": {
  "path": "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "name": "",
  "new_window": true
}

// As a shorthand string — a path…
"browser": "C:/Program Files/Google/Chrome/Application/chrome.exe"
// …or a webbrowser name:
"browser": "firefox"
```

| Key | Description |
|-----|-------------|
| `path` | **Recommended.** An explicit browser executable. For Chromium browsers (Chrome/Edge/Brave/…) the app adds `--new-window` so all of a room's URLs open as tabs in one fresh window. |
| `name` | A Python `webbrowser` registered name (e.g. `"firefox"`). The app also tries to resolve common names (`chrome`, `edge`, `brave`, …) to an executable on `PATH` so `--new-window` still works. |
| `new_window` | Request a new window (default `true`). Without an explicit `path` this is only a hint — the OS handler decides window-vs-tab. |
| *(neither path nor name)* | The OS default browser is used. |

If a configured executable fails to launch, the app logs a warning and falls
back to the `webbrowser` module / OS default rather than failing.

---

## Per-user state

Separate from `config.json`, mission-deck keeps a `state.json` in the per-user
config directory. It is **best-effort**: a missing or corrupt file never stops
the app — it falls back to defaults, and only known keys are accepted.

| Field | Default | Purpose |
|-------|---------|---------|
| `last_config_path` | `null` | The config to re-open on next launch. |
| `recent_configs` | `[]` | Move-to-front list (max 8) for the welcome screen's "Recent". |
| `ping_timeout_seconds` | `null` | Override for the status-check timeout. |
| `max_concurrent_checks` | `0` | Override for the simultaneous-probe cap (`0` = use config/built-in default). |
| `auto_refresh_enabled` | `false` | Auto-refresh on/off. |
| `auto_refresh_seconds` | `60` | Auto-refresh interval. |
| `browser_path` | `""` | Browser override from Settings. |
| `browser_new_window` | `true` | New-window preference. |
| `appearance` | `"dark"` | `dark` / `light` / `system`. |
| `start_on_dashboard` | `true` | Open on the Overview instead of a room. |
| `dashboard_poll_enabled` | `false` | Background estate-wide status sweeps on/off. |
| `dashboard_poll_seconds` | `120` | Interval between background sweeps. |
| `history_retention_days` | `30` | Prune uptime-history samples older than this. |

Values set in the **Settings** dialog write here and take precedence over the
`app` block in the config file. This is why a read-only EXE install still lets
each operator keep their own preferences.

---

## A minimal config

```json
{
  "schema_version": 1,
  "rooms": [
    {
      "id": "courtroom-1",
      "name": "Courtroom 1",
      "devices": [
        {
          "id": "1-crestron",
          "name": "Crestron Processor",
          "type": "control_processor",
          "host": "192.168.1.10",
          "port": 41794,
          "protocol": "tcp"
        }
      ]
    }
  ]
}
```

For a fully-worked multi-city example with AV-over-IP and VC codecs, see the
repository's `config.example.json`.
