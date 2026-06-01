# mission-deck

A modern, open-source desktop tool for managing courtroom AV equipment — a
Python/CustomTkinter replacement for the legacy PowerShell GUI script.

mission-deck gives AV technicians a single dark-mode dashboard to browse every
room's equipment, check whether devices are online, and **open all of a room's
web UIs in the browser with one click** (the headline feature of the original
tool).

> **Security by design:** application logic is strictly decoupled from
> environment data. No IP addresses or device names live in this repository —
> they live only in a local, git-ignored `config.json`. The repo ships a
> `config.example.json` containing dummy data.

---

## Features

- **External JSON config** — rooms and devices are loaded, validated and
  displayed from a local `config.json`. If none is found, the app prompts you
  to select one.
- **City → Room → Device hierarchy** — rooms are organised into collapsible
  **city boxes** in the sidebar (Melbourne, Sydney, …), so large estates
  (100+ rooms) stay navigable. A search box filters across city/room/location.
- **Dashboard UI** — a sidebar for room selection and a main panel showing the
  room's devices as cards grouped by category (Control Processors, PTZ Cameras,
  Audio DSPs, Displays, Document Cameras, Recorders, …).
- **Open Web UIs** — opens every web-accessible device in the selected room in
  your configured browser, popping out in a new window.
- **Async status checker** — a "Check Status" action that probes all devices in
  a room concurrently (async TCP connect with a configurable timeout) on a
  background thread, flipping each indicator green (online) or red (offline)
  live as results arrive — without freezing the UI.
- **Modular device classes** — a clean, registry-based class structure so new
  device types and control commands (HTTP/TCP to a Crestron, PTZ camera, etc.)
  can be added easily.

## Requirements

- **Python 3.11+**
- Dependencies (see `requirements.txt`):
  - [`customtkinter`](https://customtkinter.tomschimansky.com/) — modern dark UI
  - `pillow` — image/icon rendering

Status checks use the standard-library `asyncio`/`socket`, so no networking
dependency is required.

## Installation

```bash
git clone <your-fork-url> mission-deck
cd mission-deck

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Configuration

1. Copy the example and fill in your real infrastructure:

   ```bash
   cp config.example.json config.json
   ```

2. Edit `config.json`. **It is git-ignored and must never be committed.**

mission-deck looks for a config in this order (first match wins):

1. The path in the `MISSION_DECK_CONFIG` environment variable
2. `config.json` in the current working directory
3. `config.json` next to the application
4. The per-user config dir (`%APPDATA%\mission-deck\` on Windows,
   `~/.config/mission-deck/` on Linux, `~/Library/Application Support/mission-deck/` on macOS)

### Config schema (`schema_version: 1`)

```jsonc
{
  "schema_version": 1,
  "app": {
    "ping_timeout_seconds": 2.0,
    "browser": {
      "path": "C:/Program Files/Google/Chrome/Application/chrome.exe",
      "name": "",
      "new_window": true
    }
  },
  "rooms": [
    {
      "id": "courtroom-1a",
      "name": "Courtroom 1A",
      "city": "Melbourne",          // groups the room in the sidebar
      "location": "1st Floor - East Wing",
      "notes": "Main felony trial courtroom.",
      "devices": [
        {
          "id": "1a-crestron-cp4",
          "name": "Crestron Control Processor",
          "type": "control_processor",
          "manufacturer": "Crestron",
          "model": "CP4",
          "host": "10.10.1.10",
          "port": 41794,            // control/status port
          "protocol": "tcp",
          "web_protocol": "http",   // optional: web UI lives elsewhere…
          "web_port": 80,           // …than the control port
          "tags": ["control"]
        }
      ]
    }
  ]
}
```

#### Device fields

| Field | Required | Notes |
|-------|----------|-------|
| `id` | yes | Unique within its room |
| `name` | yes | Display name |
| `type` | yes | One of the registered types (see below); unknown types render as "Generic Device" |
| `host` | yes | Hostname or IP |
| `port` | no | Defaults per protocol (http→80, https→443, ssh→22). Used for status checks |
| `protocol` | no | `tcp` (default), `http`, `https`, `ssh`, `telnet` |
| `manufacturer`, `model` | no | Shown on the device card |
| `tags` | no | List of strings |
| `web_url` | no | Explicit web UI URL (overrides everything below) |
| `web_protocol` / `web_port` / `web_path` | no | Build a web UI URL separately from the control port |

**Control port vs web UI:** a device's *status check* uses `protocol`/`port`,
while **Open Web UIs** uses `web_url` (or `web_protocol`/`web_port`/`web_path`,
falling back to `protocol`/`port` if those are web schemes). Devices with no web
scheme (e.g. SSH/raw-TCP only) are simply skipped when opening web UIs.

**Registered device types:** `control_processor` (aka `crestron`, `controller`),
`ptz_camera` (`camera`), `audio_dsp` (`dsp`), `display` (`monitor`, `tv`),
`document_camera` (`doc_camera`), `recorder`.

### Browser configuration

`app.browser` controls how **Open Web UIs** launches:

- `path` — an explicit browser executable. **Recommended.** For Chromium
  browsers (Chrome/Edge/Brave) it passes `--new-window`, so all of a room's
  device UIs open as tabs in one fresh window.
- `name` — a [`webbrowser`](https://docs.python.org/3/library/webbrowser.html)
  registered name (e.g. `"firefox"`).
- *(neither)* — the operating-system default browser.
- `new_window` — request a new window (default `true`).

## Running

```bash
python -m mission_deck
```

If no `config.json` is found you'll be prompted to pick one; cancel to browse
the bundled example data.

## Project structure

```
mission-deck/
├── config.example.json      # dummy data (the only config in the repo)
├── requirements.txt
└── mission_deck/
    ├── __init__.py          # version / app metadata
    ├── __main__.py          # `python -m mission_deck` entry point
    ├── config.py            # locate / load / structurally validate config
    ├── models.py            # Site → Room → Device typed models + registry
    ├── browser.py           # open web UIs in the configured browser
    ├── theme.py             # dark palette + sizing tokens
    └── app.py               # CustomTkinter UI
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The most important rule: **never commit
real environment data** — keep it in your git-ignored `config.json`.

## License

See [LICENSE](LICENSE) if present; otherwise this project is intended to be
open source — add a license of your choice before distributing.
