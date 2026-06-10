# Configuration Guide

Mission Deck reads its configuration from a `config.json` file. An annotated example is provided at `config.example.json`.

---

## File Discovery

The program searches for `config.json` in this order (first found wins):

1. The path set in the **`MISSION_DECK_CONFIG`** environment variable (if defined)
2. The **current working directory**
3. The **application/repository root** (where mission-deck is installed)
4. The **per-user config directory**:
   - **Windows:** `%APPDATA%\mission-deck`
   - **macOS:** `~/Library/Application Support/mission-deck`
   - **Linux:** `~/.config/mission-deck`

If no config file is found, the GUI prompts you to select one manually.

---

## Schema Version

Every config file must declare a top-level `schema_version`. The current supported version is **`1`**. The program will reject files with a missing or unsupported version.

---

## Top-Level Structure

```jsonc
{
  "schema_version": 1,
  "app": { /* see App Settings */ },
  "rooms": [ /* see Rooms */ ]
}
```

| Key | Type | Required | Description |
|---|---|---|---|
| `schema_version` | integer | **yes** | Must be `1` for this build |
| `app` | object | no | Application-wide settings |
| `rooms` | array of objects | **yes** | One or more room definitions |

---

## App Settings

Optional global knobs placed under the `"app"` key:

| Key | Type | Default | Description |
|---|---|---|---|
| `ping_timeout_seconds` | number | `2.0` | Seconds to wait for a device probe before marking it offline |
| `max_concurrent_checks` | integer | `0` (→ built-in default of `128`) | Cap on how many status probes run at once. Bounds an estate-wide sweep so it never opens thousands of sockets at once. A per-user Settings value overrides this. |
| `default_appearance` | string | — | Preferred UI theme (`"dark"`, `"light"`, etc.) |
| `browser` | string or object | — | How **Open Web UIs** launches the browser (see [Browser Configuration](#browser-configuration)) |

Example:
```json
"app": {
  "ping_timeout_seconds": 2.0,
  "max_concurrent_checks": 128,
  "default_appearance": "dark",
  "browser": { "path": "", "name": "", "new_window": true }
}
```

---

## Rooms

Each object in the `rooms` array represents a physical space (e.g., a courtroom).

| Key | Type | Required | Description |
|---|---|---|---|
| `id` | string | **yes** | Unique identifier for the room (e.g. `"courtroom-1a"`). Must be unique across all rooms. |
| `name` | string | **yes** | Human-readable label (e.g. `"Courtroom 1A"`) |
| `city` | string | no | Groups the room into a collapsible **city box** in the sidebar. If no room has a `city`, the sidebar shows a flat list. |
| `location` | string | no | Physical location (e.g. `"1st Floor - East Wing"`). Also searchable. |
| `notes` | string | no | Free-text notes about the room |
| `devices` | array of objects | no | Devices installed in this room (see below) |

---

## Devices

Each object in a room's `devices` array represents one piece of AV equipment.

### Required Fields

| Key | Type | Description |
|---|---|---|
| `id` | string | Unique device ID within its room (e.g. `"1a-crestron-cp4"`) |
| `name` | string | Human-readable label (e.g. `"Crestron Control Processor"`) |
| `type` | string | Device type — pick from the [supported types](#supported-device-types) below |
| `host` | string | IP address or hostname (e.g. `"10.10.1.10"`) |

### Optional Fields

| Key | Type | Default | Description |
|---|---|---|---|
| `port` | integer `0-65535` | protocol default | Network port. If omitted, sensible defaults are used (`80` for http, `443` for https, `22` for ssh, `23` for telnet, `0` for raw tcp — which *must* supply an explicit port). |
| `protocol` | string | `"tcp"` | Transport protocol: `"tcp"`, `"http"`, `"https"`, `"ssh"`, or `"telnet"` |
| `manufacturer` | string | `""` | Manufacturer name (e.g. `"Crestron"`) |
| `model` | string | `""` | Model number/name (e.g. `"CP4"`) |
| `tags` | array of strings | `[]` | Arbitrary tags for grouping/filtering (e.g. `["camera","video"]`) |
| `web_url` | string | — | Explicit web-UI URL; overrides all web derivation below |
| `web_protocol` / `web_port` / `web_path` | — | — | Build a web-UI URL separately from the control port (e.g. control on TCP/41794 but admin page on HTTP/80) |
| `monitor` | string | `"tcp"` | How reachability is judged: `tcp` (TCP connect), `http`/`https` (any answered endpoint = up), or `ping` (one ICMP echo via the system ping — for devices with no open TCP port) |
| `health_url` | string | — | URL the `http`/`https` monitor probes (falls back to the device's `web_url`) |
| `commands` | array | — | Config-driven control buttons — see [Control Commands](#control-commands) |

> **Control port vs. web UI:** the status check uses the device's *monitor*
> (the `tcp` monitor probes `protocol`/`port`; the `http`/`https` monitor probes
> `health_url`/`web_url`; the `ping` monitor needs only `host`), while
> **Open Web UIs** uses the resolved web URL.
> Devices with no web scheme (SSH/raw-TCP only) are skipped when opening web UIs.

Any **unknown keys** are preserved in the device's `extra` map for
forward-compatibility and written back verbatim on save (`web_*`, `commands`,
and recorder URLs all ride on `extra`).

### Supported Device Types

The `type` field accepts any of the following values (case-insensitive). Unknown types are still loaded as a Generic Device and won't break the config.

| type value | Display category |
|---|---|
| `ptz_camera`, `camera` | PTZ Camera |
| `control_processor`, `crestron`, `controller` | Control Processor |
| `audio_dsp`, `dsp` | Audio DSP |
| `display`, `monitor`, `tv` | Display |
| `document_camera`, `doc_camera` | Document Camera |
| `recorder` | Recorder (supports recording controls — see below) |
| `video_matrix`, `matrix_switcher`, `av_matrix`, `blustream`, `nvx`, `extron_matrix`, `amx_matrix`, `atlona`, `wyrestorm` | Video Matrix (AV-over-IP) |
| `video_encoder`, `av_encoder`, `encoder`, `tx`, `blustream_tx`, `nvx_tx` | Video Encoder (TX) |
| `video_decoder`, `av_decoder`, `decoder`, `rx`, `blustream_rx`, `nvx_rx` | Video Decoder (RX) |
| `vc_codec`, `video_conference`, `codec`, `cisco_webex`, `webex`, `poly`, `lifesize`, `zoom_room` | Video Conferencing |
| *(anything else)* | Generic Device |

---

## Recorder Fields

A `recorder` device supports live recording status and start/stop controls via
these optional keys:

| Key | Description |
|---|---|
| `recording_status_url` | URL polled (GET) to read recording state |
| `recording_status_json_path` | Dot-path into the JSON response, e.g. `"state.recording"` (empty = whole body) |
| `recording_start_url` | URL invoked to start recording |
| `recording_stop_url` | URL invoked to stop recording |

The status value is interpreted leniently (booleans, `0/1`, and strings like
`recording`/`active`/`on` → **Recording**; `paused`/`suspended` → **Paused**;
`idle`/`stopped`/`off` → **Idle**). Any network or parse error yields **Unknown**.

---

## Control Commands

Every entry in a device's `commands` array becomes a **button** in the device's
control panel — no code required. Describe the actual HTTP request or TCP payload
and mission-deck stays vendor-neutral.

| Key | Applies to | Description |
|---|---|---|
| `id` / `label` | all | Identifier / button text |
| `protocol` | all | `http`, `https`, or `tcp` (default `tcp`) |
| `prompt` | all | When set, the UI asks the operator for a value first (fills `{value}`) |
| `timeout` | all | Seconds before the command gives up (default `5.0`) |
| `url` | http/https | **Required.** Request URL (placeholders substituted) |
| `method` / `body` / `headers` | http/https | HTTP verb (default `GET`), request body, extra headers |
| `auth` | http/https | `{"username": …, "password": …}` → an `Authorization: Basic` header |
| `verify_tls` | http/https | `false` to skip certificate verification (self-signed LAN codecs) |
| `payload` | tcp | **Required.** Bytes to send (placeholders substituted, UTF-8) |
| `port` | tcp | Override the device's control port for this command |
| `read_response` | tcp | `true` to wait for and display a short reply |

Placeholders `{host}`, `{port}`, and `{value}` are substituted at runtime in the
`url`, `payload`, and `body`.

> 🔐 **Codec credentials** in `auth` are real secrets — they belong only in your
> git-ignored `config.json`, never in `config.example.json`.

```jsonc
"commands": [
  { "id": "preset1", "label": "Camera Preset 1", "protocol": "http",
    "url": "http://{host}/cgi-bin/ptzctrl?action=recall&preset=1" },
  { "id": "power_on", "label": "Power On", "protocol": "tcp",
    "payload": "PWR ON\r", "port": 4998 },
  { "id": "dial", "label": "Place Call…", "protocol": "https", "method": "POST",
    "url": "https://{host}/putxml",
    "body": "<Command><Dial><Number>{value}</Number></Dial></Command>",
    "prompt": "Address or number to dial",
    "auth": {"username": "admin", "password": "changeme"}, "verify_tls": false }
]
```

---

## Browser Configuration

The `app.browser` setting controls **Open Web UIs**. It accepts a string (a path
*or* a `webbrowser` name) or an object:

| Key | Description |
|---|---|
| `path` | **Recommended.** Explicit browser executable. Chromium browsers get `--new-window` so a room's URLs open as tabs in one fresh window. |
| `name` | A Python `webbrowser` registered name (e.g. `"firefox"`). |
| `new_window` | Request a new window (default `true`). |
| *(neither)* | The OS default browser is used. |

---

## Per-User State

Separate from `config.json`, mission-deck stores per-user preferences and recent
files in a best-effort `state.json` in the per-user config directory. Values set
in the **Settings** dialog (status-check timeout, max concurrent checks,
auto-refresh, browser, appearance) and dashboard options (open on Overview,
background sweep poll, history retention days) are saved here and **override** the
`app` block at runtime. A missing or corrupt `state.json` falls back to defaults.

---

## Quick Start

Copy the example config and edit it for your site:

```bash
cp config.example.json config.json
```

Then fill in real IPs, device names, and room details. A minimal single-room config:

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
