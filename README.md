# mission-deck

A modern, open-source desktop tool for managing courtroom AV equipment — a
Python/CustomTkinter replacement for a legacy PowerShell GUI script.

mission-deck gives AV technicians a single dark-mode dashboard to browse every
room's equipment, check whether devices are online, watch estate-wide health and
uptime at a glance, and **open all of a room's web UIs in the browser with one
click** (the headline feature of the original tool).

<img width="1916" height="1033" alt="image" src="https://github.com/user-attachments/assets/9b3b321f-e279-4460-8c01-cf508e6da5b3" />



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
- **Room view** — a sidebar for room selection and a main panel showing the
  room's devices as cards grouped by category (Control Processors, PTZ Cameras,
  Audio DSPs, Displays, Document Cameras, Recorders, Video Matrix/Encoders/
  Decoders, Video Conferencing, …).
- **Estate Overview** — a second top-level view (sidebar "⌂ Overview") that
  answers "how is the whole estate right now?" as a flat, single-column report:
  a headline stat strip (rooms, devices, online, offline, healthy rooms), a
  "needs attention" list of offline devices, an estate-wide recorders section,
  per-room **uptime over the last 24h**, and a recent-activity feed from the
  audit log. "Refresh All" sweeps every room at once, an optional background
  poll keeps it live, and **Export** saves the whole estate's status (per-device
  status, latency, 24h uptime) as a CSV for handover/reporting.
- **Uptime history** — every status check (per-room or estate-wide) is persisted
  to a local SQLite database, so the Overview can report real reachability
  percentages and trends rather than just a momentary snapshot.
- **Open Web UIs** — opens every web-accessible device in the selected room in
  your configured browser, popping out in a new window.
- **Async status checker** — a "Check Status" action that probes all devices in
  a room concurrently on a background thread, flipping each indicator green
  (online) or red (offline) live as results arrive — without freezing the UI.
  Concurrency is bounded so an estate-wide sweep of thousands of devices never
  opens thousands of sockets at once.
- **Pluggable monitors** — how a device's reachability is judged is itself a
  registry: the default `tcp` monitor opens a TCP connection, `http`/`https`
  monitors treat any answered endpoint as up, and the `ping` monitor sends one
  ICMP echo via the system ping (for devices with no open TCP port). A device
  opts in with a `"monitor"` config key; new check types are one decorator away.
- **Auto-refresh** — an optional toggle re-runs the status check for the current
  room on an interval, so the dashboard stays live hands-free.
- **Device control** — click any device card to open its control panel. Actions
  are vendor-neutral and **config-driven**: describe an HTTP URL or TCP payload
  in the device's `commands` list and it becomes a button (camera presets,
  power on/off, route AV matrix outputs, drive a VC codec, custom commands).
  Commands run off the UI thread.
- **In-app editing** — add, edit, duplicate, or remove rooms, devices, and
  command buttons through form dialogs; changes are written back to your
  `config.json` atomically (no hand-editing of JSON required).
- **Modular device classes** — a clean, registry-based class structure so new
  device types can be added with a single decorator.

### Built for non-technical users

- **No JSON required day-to-day** — a friendly **welcome screen** (not a bare
  file dialog) lets staff open a config, pick a **recent** one, or explore demo
  data. The last-opened config is **remembered**, so the app just opens to it
  next launch.
- **Settings dialog (⚙)** — appearance (dark/light/system), status-check
  timeout, auto-refresh interval, and the browser to use (with a Browse button)
  are all editable in the GUI and saved per-user. Switching to a different
  config is a menu click.
- **Ships as a single `.exe`** — one double-clickable file, no Python install
  (see [Packaging](#packaging-a-single-exe)).

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
    "max_concurrent_checks": 128,   // cap on simultaneous probes (0 = default)
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
| `monitor` | no | How reachability is judged: `tcp` (default), `http`, `https`, or `ping` |
| `health_url` | no | URL the `http`/`https` monitor probes (else the device's `web_url`) |
| `commands` | no | List of control actions (buttons) for the device — see below |

**Control port vs web UI:** a device's *status check* uses its monitor
(`tcp` probes `protocol`/`port`; `http`/`https` probe `health_url`/`web_url`),
while **Open Web UIs** uses `web_url` (or `web_protocol`/`web_port`/`web_path`,
falling back to `protocol`/`port` if those are web schemes). Devices with no web
scheme (e.g. SSH/raw-TCP only) are simply skipped when opening web UIs.

**Registered device types:** `control_processor` (aka `crestron`, `controller`),
`ptz_camera` (`camera`), `audio_dsp` (`dsp`), `display` (`monitor`, `tv`),
`document_camera` (`doc_camera`), `recorder`, `video_matrix` (AV-over-IP matrix —
`blustream`, `nvx`, `atlona`, …), `video_encoder` (`tx`), `video_decoder`
(`rx`), and `vc_codec` (video-conferencing — `cisco_webex`, `poly`, `zoom_room`,
…). Unknown types render as a Generic Device.

#### Device control commands

Clicking a device card opens a control panel. Besides the built-in "Open Web
UI", every entry in the device's `commands` list becomes a button — no code
required:

```jsonc
"commands": [
  {"id": "preset1", "label": "Camera Preset 1", "protocol": "http",
   "url": "http://{host}/cgi-bin/ptzctrl?action=recall&preset=1"},
  {"id": "power_on", "label": "Power On", "protocol": "tcp",
   "payload": "PWR ON\r", "port": 4998},
  {"id": "send", "label": "Send Command", "protocol": "tcp",
   "payload": "{value}\r", "prompt": "Command to send", "read_response": true}
]
```

- `protocol`: `http`/`https` (issues a GET to `url`) or `tcp` (sends `payload`).
- Placeholders `{host}`, `{port}`, `{value}` are substituted at run time.
- `prompt`: when set, the UI asks the user for a value (`{value}`) first.
- `read_response` (tcp): wait for and display a short reply.
- `port` (tcp): override the device's control port for this command.

Commands run on a background thread, so a slow or unreachable device never
freezes the UI; the result/error is shown in the control panel.

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

On first launch (no config found) a welcome screen lets you open a config file
or explore the demo data. After that, the app reopens your last config
automatically.

## Logging & auditing

mission-deck keeps two rotating log files in a writable per-user directory
(`%APPDATA%\mission-deck\logs` on Windows, `~/.config/mission-deck/logs` on
Linux, `~/Library/Application Support/mission-deck/logs` on macOS):

- **`mission-deck.log`** — diagnostic detail for support and troubleshooting
  (config loading, device probes, control transports, browser launches, and full
  tracebacks for anything unexpected — including crashes on background threads).
- **`audit.log`** — an append-only, JSON-per-line record of operator actions for
  compliance: every device command issued (and its outcome), web UIs opened,
  status-check summaries, configuration loads/saves/switches, and settings
  changes. Each entry records the timestamp, action, and OS username.

Environment overrides:

| Variable | Effect |
|----------|--------|
| `MISSION_DECK_CONFIG` | Absolute path to the config file to load (highest-priority discovery location). |
| `MISSION_DECK_LOG_DIR` | Write both logs to a custom directory. |
| `MISSION_DECK_LOG_LEVEL` | Diagnostic verbosity (`DEBUG`, `INFO`, `WARNING`, …). Default `INFO`. Use `DEBUG` to trace individual probes and commands. |
| `MISSION_DECK_HISTORY_DB` | Path to the uptime-history SQLite database (default: per-user dir `history.db`). |

Logs rotate at ~2 MB (5 generations kept), so they never run the disk out of
space even on a long-lived install. If the log directory can't be created, the
app degrades gracefully to console logging rather than failing to start.

## Packaging a single EXE

The app is built to ship as one double-clickable `.exe` so non-technical users
don't need Python.

```powershell
pip install -r requirements-dev.txt
.\build.ps1            # or: pyinstaller mission-deck.spec
```

This produces `dist\mission-deck.exe` (~19 MB, windowed/no console). Notes:

- `config.example.json` is bundled (powers "Explore Demo Data"); CustomTkinter's
  theme assets are collected automatically by the spec.
- The user's **real** `config.json` is *not* bundled — it lives next to the EXE
  or in `%APPDATA%\mission-deck`, and the chosen file is remembered between runs.
- Per-user preferences are stored in `%APPDATA%\mission-deck\state.json`, which
  keeps the install itself read-only-friendly.
- To brand the EXE, set an `.ico` path in `mission-deck.spec` (`icon=`).

## Project structure

```
mission-deck/
├── config.example.json      # dummy data (the only config in the repo)
├── requirements.txt
├── requirements-dev.txt     # + pyinstaller, for building the EXE
├── mission-deck.spec        # PyInstaller build spec
├── build.ps1                # one-command EXE build
└── mission_deck/
    ├── __init__.py          # version / app metadata
    ├── __main__.py          # `python -m mission_deck` entry point
    ├── config.py            # locate / load / structurally validate config
    ├── models.py            # Site → Room → Device typed models + registry
    ├── network.py           # async status checks (monitor registry) + control transports
    ├── controls.py          # per-device control actions (config-driven)
    ├── browser.py           # open web UIs in the configured browser
    ├── history.py           # persisted uptime-history store (SQLite)
    ├── dashboard.py         # estate-wide Overview view (flat report: stats, uptime, activity)
    ├── report.py            # estate status report export (CSV)
    ├── state.py             # remembered config + GUI-managed preferences
    ├── theme.py             # dark palette + sizing tokens
    ├── logging_setup.py     # diagnostic + audit logging configuration
    ├── editors.py           # in-app room/device/command editor dialogs
    └── app.py               # CustomTkinter UI + event orchestration
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The most important rule: **never commit
real environment data** — keep it in your git-ignored `config.json`.

## License

See [LICENSE](LICENSE) if present; otherwise this project is intended to be
open source — add a license of your choice before distributing.
