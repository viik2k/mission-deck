# User Guide

This guide is for **AV technicians and operators** who use mission-deck day to
day. No programming or JSON knowledge is required — everything below is done
through the graphical interface.

If you are installing the app for the first time, see
[Installation & Setup](Installation-and-Setup.md). If you need to change which
devices appear, see the [Configuration Reference](Configuration-Reference.md).

---

## 1. Launching the app

How you start mission-deck depends on how it was deployed to your machine:

- **Packaged build:** double-click `mission-deck.exe`. No Python install is
  needed.
- **From source:** run `python -m mission_deck` from the project directory
  (see [Installation & Setup](Installation-and-Setup.md)).

### What happens on first launch

mission-deck looks for a configuration file in several standard locations (see
[Config discovery](Configuration-Reference.md#config-discovery-order)). What you
see next depends on whether one is found:

- **A config is found** → the app opens straight to it (on the **Overview** by
  default — see [§2a](#2a-the-overview-estate-wide-health)).
- **You opened a config before** → the app re-opens that same file
  automatically. You don't have to pick it again.
- **Nothing is found and you've never chosen one** → the **Welcome screen**
  appears.

### The Welcome screen

The welcome screen is a friendly alternative to a bare file-picker. It gives you
three choices:

| Option | What it does |
|--------|--------------|
| **Open a config file…** | Browse to and open a `config.json` for your site. |
| **Recent** | Re-open a config you've used before (the most recent are listed). |
| **Explore Demo Data** | Open the bundled `config.example.json` — fictional courtrooms with dummy IPs, so you can try the app safely without any real infrastructure. |

Whatever you open is **remembered**, so the next launch skips the welcome screen
and goes straight to your dashboard.

---

## 2. The window layout

The window stacks top to bottom: a **top navigation bar**, a slim **context
bar**, then the active view.

```
┌──────────────────────────────────────────────────────────────────────┐
│ MISSION-DECK   Overview  Rooms  Dashboards  Plugins   2 OFFLINE  🔍 ⚙ ▢ │  ← top nav bar
├──────────────────────────────────────────────────────────────────────┤
│ Rooms › Melbourne › Courtroom 1A          Check Status   Open Web UIs   │  ← context bar
├───────────────┬──────────────────────────────────────────────────────┤
│  room sidebar │   DEVICE CARDS, grouped by category                    │
│  search box   │   ┌───────────┐ ┌───────────┐ ┌──────────┐            │
│  ▸ Melbourne  │   │ Card      │ │ Card      │ │ Card     │            │
│    Courtroom1A│   └───────────┘ └───────────┘ └──────────┘            │
│  ▸ Sydney     │                                                        │
└───────────────┴──────────────────────────────────────────────────────┘
```

- **Top nav bar:** the brand mark, the labelled view tabs (**Overview**,
  **Rooms**, **Dashboards**, **Plugins** — plus any plugin views you've enabled),
  a live **"N OFFLINE"** attention badge that jumps to the Overview, the **search**
  button (Ctrl+K command palette), the **settings** gear, and the operator chip.
- **Context bar:** a breadcrumb of where you are plus the per-view actions (in the
  Rooms view, **Check Status** and **Open Web UIs**).
- **Rooms view:** a **room sidebar** (a search box and the rooms grouped into
  collapsible **city boxes** — e.g. Melbourne, Sydney, Brisbane) beside the
  selected room's devices, shown as **cards** grouped by category (Control
  Processors, PTZ Cameras, Audio DSPs, Displays, Document Cameras, Recorders,
  Video Matrix, Video Encoders/Decoders, Video Conferencing, …).

Click any tab in the top nav bar to switch views; click a room in the Rooms
sidebar to open it. Keyboard shortcuts: **Ctrl+K**/**Ctrl+P** command palette,
**Ctrl+1..4** switch views, **Ctrl+F** filter rooms, **F5** re-probe the current
view, **Ctrl+,** settings, **F1** the shortcut reference.

---

## 2a. The Overview (estate-wide health)

The **Overview** answers "how is the *whole* estate right now?" without clicking
through every room. By default the app opens here (you can change that in
Settings). It shows:

- **Stat strip** — total rooms, total devices, how many are **online** /
  **offline**, and how many rooms are fully **healthy**.
- **Needs attention** — every device currently **offline**, with its room and
  address. Click a row to jump straight into that room.
- **Recorders** — the recording state (Idle / Recording / Paused) of every
  recorder across the estate — handy during hearings.
- **Uptime · last 24h** — each room's reachability percentage over the last 24
  hours, worst first, drawn as a small bar. This is built from saved history, so
  it reflects trends, not just the latest check.
- **Recent activity** — the tail of the audit log: who did what, most recent
  first.

### Refreshing the Overview

- **Refresh All** sweeps **every device in every room** at once (bounded so it
  stays gentle on the network) and updates every panel live. The button shows
  "Sweeping…" while it runs, and a "last sweep HH:MM:SS" stamp when done.
- The **Auto** switch turns on a background sweep that re-runs on an interval, so
  a wall display stays current hands-free.
- **Export** writes a CSV status report (per-device status, latency and 24h
  uptime) to a location you pick — handy for a shift handover or a ticket.

> Uptime needs data: a room shows "—" until at least one sweep/check has recorded
> a sample for it.

---

## 2b. Dashboards (your own board)

The **Dashboards** tab is a board you compose yourself. Click **Add widget** to
pick from a catalogue — KPI tiles, 24-hour **uptime** and **latency** trend bars,
and offline / recorder / room-uptime / recent-activity lists — then drag to
reorder or remove widgets in place. Your layout is saved per-operator and
restored next launch. Like the Overview, the board refreshes whenever a sweep or
room check finishes.

---

## 2c. Plugins (optional features)

The **Plugins** tab is where you turn optional features on and off. The catalogue
shows the four built-in **monitors** (TCP, HTTP/HTTPS, Ping, TLS — always on, used
per device via the `monitor` config key) and two toggleable plugins:

- **Cloud Sync** — pull `config.json` from a central **HTTPS** URL (a OneDrive /
  SharePoint direct link, an intranet path, a Git raw URL) so every workstation
  loads the same estate. Enabling it adds a **Cloud Sync** tab where you enter the
  URL; the config is validated, cached locally and audited, then loaded via a
  soft-restart.
- **Activity Log** — adds an **Activity** tab: a full-screen, filterable reader of
  the local audit trail (who ran which command, on which device, when, and whether
  it succeeded). It reads `audit.log` only — nothing leaves the workstation.

Enabling or disabling a plugin shows or hides its tab in the top nav bar
immediately; the choice persists per operator.

---

## 3. Finding and selecting a room

### City groups

Rooms are organised into **city boxes** in the sidebar. Click a city header to
expand or collapse it. This keeps large estates (100+ rooms) navigable — you can
fold away the sites you aren't working on.

> If your config has no city information anywhere, the sidebar shows a flat list
> of rooms instead of a single pointless "Ungrouped" header.

### Searching

Type in the **search box** at the top of the sidebar to filter rooms instantly.
The filter matches across a room's **name**, **city**, and **location**, so
"east wing", "Sydney", or "courtroom 2" all narrow the list. City groups that
contain no matches are hidden while you search; clearing the box restores the
full list.

### Selecting a room

Click a room to load its devices into the main panel. The selected room is
highlighted, and each room button carries a small **health dot** summarising the
status of all its devices (see [Room health](#5-checking-device-status)).

---

## 4. Device cards

Each device in the selected room appears as a **card** showing:

- the device **name**;
- a short **manufacturer / model** description;
- the network **address** (`host:port`);
- a **status indicator** dot (grey / amber / green / red — see below).

Cards are grouped under category headings. Recorders additionally show a
**recording status** badge (Idle / Recording / Paused) when recording polling is
configured for them.

**Click any card** to open its **control panel** (covered in
[§7 Device controls](#7-device-controls)).

---

## 5. Checking device status

Click **Check Status** in the header to probe every device in the current room.

### How it works (operator's view)

mission-deck opens a quick network connection to each device **at the same
time** (concurrently) and waits up to a configurable timeout. As each device
answers, its card updates **live** — you don't wait for the whole room to
finish. The UI never freezes during a check.

### Reading the indicators

| Colour | Meaning |
|--------|---------|
| 🟢 **Green** | Online — the device accepted a connection. Latency may be shown. |
| 🔴 **Red** | Offline — the connection timed out or was refused/unreachable. |
| 🟡 **Amber** | Checking — a probe is in flight, or a room's devices are mixed (some up, some down). |
| ⚪ **Grey** | Unknown — not checked yet, or there's no port to test. |

The **room health dot** in the sidebar aggregates these: green if every device
is online, red if every device is offline, amber if it's a mix or a check is
running, and grey if nothing has been checked.

> **What "online" really means:** for most devices a green dot means mission-deck
> could open a TCP connection to the device's configured port — not that the
> device's application is fully healthy, just that something is listening.
> (Devices configured with an `http`/`https` *monitor* instead count as online
> when their web endpoint answers.) See
> [Networking & Device Control](Networking-and-Device-Control.md) for detail.

### Auto-refresh

In **Settings** you can enable **Auto-refresh**, which re-runs the status check
for the current room on a fixed interval (e.g. every 60 seconds). This keeps the
dashboard live hands-free — useful on a wall display during a hearing. Switching
rooms re-bases the timer on the newly selected room.

---

## 6. Opening web UIs (the headline feature)

Click **Open Web UIs** in the header to open the management web page of **every
web-accessible device in the room at once**, in your configured browser.

This is the feature that replaced the legacy PowerShell tool: instead of typing
a dozen IP addresses by hand, one click pops them all open.

- For **Chrome / Edge / Brave** and other Chromium browsers, all the URLs open
  as tabs in a **single new window** (the app passes `--new-window`).
- Devices with **no web interface** (for example an SSH-only DSP or a raw-TCP
  display) are simply **skipped** — only devices that actually have a web UI are
  opened.
- The browser launch happens in the background, so the app stays responsive even
  when opening many tabs.

Which browser is used is set in **Settings** (see below). If none is configured,
your operating system's default browser is used.

---

## 7. Device controls

Click a **device card** to open its **control panel**. The panel always offers
**Open Web UI** for web-accessible devices, plus any **custom command buttons**
that have been configured for that device.

### Custom command buttons

Command buttons are **configured per device** (by an administrator, in the
config file — no code required). Typical examples:

- **Camera Preset 1 / 2 / 3** — recall PTZ camera positions.
- **Power On / Power Off** — for displays and projectors.
- **Route Output…** — re-route an AV-over-IP video matrix.
- **Place Call… / Hang Up / Standby** — drive a Cisco Webex or Poly VC codec.

When you click a command button:

- If the command needs input (it has a **prompt**), you're asked for a value
  first — e.g. *"Address or number to dial"* or *"Routing command"*.
- The command runs **in the background**, so a slow or unreachable device never
  freezes the app.
- The **result** (success, the device's reply, or an error message) is shown in
  the control panel.

Every command you issue is recorded in the **audit log** (see
[Logging & Auditing](Logging-and-Auditing.md)), including which device, which
action, and the outcome.

### Recorder controls

A **Recorder** device's control panel can show **Start** / **Stop** buttons and
a live **recording status** (Idle / Recording / Paused), when the recorder's
status and start/stop URLs are configured. The recording badge also appears on
the device card.

### Live View (watch a camera's feed)

If a PTZ camera has a **stream URL** configured (an administrator adds a single
`stream_url` key to the device — see the
[Configuration Reference](Configuration-Reference.md#live-view-streams)), its
control panel shows a **◉ LIVE VIEW** button. Clicking it pops out a video
window showing the camera's live feed. You can also jump straight there from
the command palette: press **Ctrl+K** and type `live`.

What you'll see in the window:

- A **status pill**: `● LIVE` (green) while frames are flowing,
  `◌ CONNECTING…` (amber) while the feed starts, `● SIGNAL LOST` (red) if the
  feed stops arriving.
- A **stats line**: current frame rate, total frames, drop count, and how long
  the feed has been up.
- A **camera switcher** (when the room has more than one streaming camera) —
  picking another camera swaps the feed *in the same window*.

**One feed at a time — by design.** There is exactly one Live View window in
the whole app. Opening Live View on a second camera reuses the window and
swaps the feed. This keeps the app light: one video decoder, one repainting
surface, no matter how many cameras you check in a row.

**If the feed drops** (camera reboots, network blip, encoder pause), the
window notices within a few seconds, shows **SIGNAL LOST**, pops a toast, and
**reconnects automatically** — first quickly, then at a slower, patient pace —
until the picture returns or you close the window. A "Live view restored"
toast confirms recovery. You never need to babysit it, but the **RECONNECT**
button retries immediately if you don't want to wait.

The window's other buttons:

| Button | What it does |
|--------|--------------|
| **SNAPSHOT** | Saves the current frame as a PNG (you pick where). Handy for recording what the room looked like at a moment in time; every snapshot is written to the audit log. |
| **PIN** | Keeps the window on top of everything else — park it in a corner or on a second monitor while you work. |
| **RECONNECT** | Skips the automatic retry wait and reconnects right now. |

> **Requires ffmpeg.** Live View decodes video with the free `ffmpeg` tool. If
> it isn't installed the button tells you exactly what to do — see
> [Installation & Setup](Installation-and-Setup.md#6-environment-variables).
> Opening, closing, switching, dropping, and snapshotting a stream are all
> recorded in the [audit log](Logging-and-Auditing.md).

### Editing from the control panel

If your build allows in-app editing, the control panel may offer affordances to
**add a command**, **edit a command**, **edit the device**, or **remove the
device**. Changes are written back to your `config.json` (see
[§9 Editing your setup](#9-editing-your-setup-in-app)).

---

## 8. The Settings dialog (⚙)

Open **Settings** from the sidebar. Everything here is saved **per user** and
persists across launches — you never edit JSON for these.

| Setting | What it controls |
|---------|------------------|
| **Appearance** | Dark / Light / System theme. |
| **Status-check timeout** | How many seconds to wait for a device before marking it offline. Lower = faster checks but more false "offline"s on a slow network. |
| **Auto-refresh** | Enable/disable automatic re-checking of the current room and set the interval (seconds). |
| **Open on Overview** | Whether the app starts on the estate Overview (on) or the last/first room (off). |
| **Background sweep** | Enable/disable the Overview's automatic estate-wide sweep and set its interval (seconds). |
| **Max concurrent probes** | How many device probes run at once during a check/sweep. `0` = automatic (default 128). Lower it on a constrained network. |
| **Browser** | The browser used by **Open Web UIs**. Use the **Browse** button to point at a browser executable, or leave blank for the OS default. |
| **Switch config** | Open a different `config.json`. This triggers a quick **soft-restart** so the new file loads cleanly. |

> Preferences set here **override** the matching values in the config file, and
> are stored in a per-user `state.json` (see
> [Configuration Reference](Configuration-Reference.md#per-user-state)).

---

## 9. Editing your setup in-app

Depending on your build, mission-deck lets you manage rooms, devices, and
command buttons through dialogs — no hand-editing of JSON:

- **Add / edit / duplicate / delete a room.**
- **Add / edit / duplicate / delete a device** (with a form for type, host,
  port, protocol, web-UI overrides, etc.).
- **Add / edit / delete a command button** on a device, with validation that
  gives you a plain-English reason if something is wrong (e.g. *"An HTTP command
  needs a URL to request."*).

Saving writes your changes back to the loaded `config.json` **atomically** (a
crash mid-save can't corrupt the file), and the change is recorded in the audit
log. Unknown fields a future version might use are preserved untouched.

---

## 10. Quick troubleshooting

| Symptom | First thing to check |
|---------|----------------------|
| A device shows **Offline** but you know it's up | The status-check **timeout** may be too short, or the **port**/`protocol` in the config may be wrong for that device. Try raising the timeout in Settings. |
| **Open Web UIs** opens nothing | None of the room's devices have a web UI configured, or the configured **browser path** is invalid (it falls back to the OS default). |
| A device has **no web button** | It has no web scheme — e.g. an SSH-only or raw-TCP device. That's expected. |
| A command button reports an **error** | The device may be unreachable, or the command's address/credentials may be wrong. The exact error is shown in the control panel. |
| The app **won't open my config** | The error dialog explains why (bad JSON, wrong schema version, a malformed room/device). Fix the file or pick another. |
| A camera has **no LIVE VIEW button** | It has no `stream_url` configured. Ask your administrator to add the camera's RTSP/RTMP URL (see the [Configuration Reference](Configuration-Reference.md#live-view-streams)). |
| Live View says it **needs ffmpeg** | Install ffmpeg and put it on `PATH` (or next to `mission-deck.exe`). See [Maintenance & Troubleshooting](Maintenance-and-Troubleshooting.md#live-view-shows-no-video--keeps-saying-signal-lost). |
| Live View is stuck on **SIGNAL LOST** | The camera's stream endpoint is unreachable or the URL/credentials are wrong. The overlay shows the underlying error; see [Maintenance & Troubleshooting](Maintenance-and-Troubleshooting.md#live-view-shows-no-video--keeps-saying-signal-lost). |

For deeper diagnostics, see
[Maintenance & Troubleshooting](Maintenance-and-Troubleshooting.md) and the
[log files](Logging-and-Auditing.md).
