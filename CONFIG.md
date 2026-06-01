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
| `ping_timeout_seconds` | number | `2.0` | Seconds to wait for a device ping before marking it offline |
| `default_appearance` | string | — | Preferred UI theme (`"dark"`, `"light"`, etc.) |

Example:
```json
"app": {
  "ping_timeout_seconds": 2.0,
  "default_appearance": "dark"
}
```

---

## Rooms

Each object in the `rooms` array represents a physical space (e.g., a courtroom).

| Key | Type | Required | Description |
|---|---|---|---|
| `id` | string | **yes** | Unique identifier for the room (e.g. `"courtroom-1a"`). Must be unique across all rooms. |
| `name` | string | **yes** | Human-readable label (e.g. `"Courtroom 1A"`) |
| `location` | string | no | Physical location (e.g. `"1st Floor - East Wing"`) |
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

### Supported Device Types

The `type` field accepts any of the following values (case-insensitive). Unknown types are still loaded as a generic device and won't break the config.

| type value | Display category |
|---|---|
| `ptz_camera`, `camera` | PTZ Camera |
| `control_processor`, `crestron`, `controller` | Control Processor |
| `audio_dsp`, `dsp` | Audio DSP |
| `display`, `monitor`, `tv` | Display |
| `document_camera`, `doc_camera` | Document Camera |
| `recorder` | Recorder |
| *(anything else)* | Generic Device |

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
