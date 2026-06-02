# Networking & Device Control

This page documents how mission-deck talks to devices: the async status checker,
the one-shot control transports (HTTP and TCP), the config-driven command
system, and recorder polling. It lives in `network.py` and `controls.py`.

A guiding constraint: **mission-deck uses the Python standard library only for
networking** — `asyncio`, `socket`, `ssl`, and `urllib`. There is no
third-party HTTP client. This keeps the runtime footprint tiny and the security
surface small.

---

## 1. Status checks (reachability)

### The question being answered

A status check answers exactly one question per device:

> *Can I open a TCP connection to this device's configured port right now?*

- **Success** (the port accepts a connection) → **ONLINE**, with a measured
  latency in milliseconds.
- **Timeout** → **OFFLINE** (`"timed out"`).
- **Refused / unreachable / DNS failure** → **OFFLINE** (with the OS error).
- **No port configured** (e.g. a raw-`tcp` device with `port: 0`) → **UNKNOWN**
  (`"no port configured to check"`).

This is a deliberately lightweight, protocol-agnostic probe. It does *not* speak
HTTP, log in, or verify the application is healthy — only that something is
listening. That's enough for an at-a-glance "is it on the network?" dashboard
and keeps checks fast and side-effect-free.

### How it runs (`network.py`)

- `check_device(device, timeout)` — async; does one
  `asyncio.open_connection(host, port)` wrapped in `asyncio.wait_for(timeout)`,
  measures latency with `time.perf_counter()`, then closes the connection
  cleanly. Returns a `CheckResult(device_id, status, latency_ms, error)`.
- `_check_all(...)` — `asyncio.gather`s a probe per device so **all run
  concurrently**, calling a `publish` callback as each finishes.
- `run_status_checks(devices, timeout, publish)` — a **blocking** driver meant
  for a worker thread. It creates a fresh event loop (it's never on the main
  thread, so there's no running loop to clash with), runs `_check_all`, and
  closes the loop.

### How the UI drives it

`app.on_check_status()`:

1. Marks each device CHECKING and starts a daemon thread running
   `run_status_checks`.
2. The thread's `publish` callback pushes each `CheckResult` onto a thread-safe
   queue — it never touches a widget.
3. `app.after(...)` drains the queue on the Tk timer and applies results to the
   cards on the UI thread, flipping them green/red **live** as they stream in.
4. A **generation counter** stamps each check run; results whose generation
   doesn't match the current room/run are discarded, so a slow probe from a
   previous room can never update the wrong card.
5. When the run finishes, an `status_check.complete` audit event records the
   online/offline tally for the room.

The timeout comes from the **effective timeout**: the per-user Settings override
if set, otherwise `app.ping_timeout_seconds` from the config (default `2.0`).

---

## 2. Control transports

Control commands use simple, synchronous, timeout-bounded transports — one
command at a time, executed **off the UI thread** by `app.run_background()`.

### `http_request(...)`

The HTTP(S) transport behind every HTTP command:

```python
http_request(url, method="GET", body=None, timeout=5.0,
             auth=None, headers=None, verify_tls=True) -> str
```

- Builds a `urllib.request.Request` with the given method and optional UTF-8
  body.
- `auth=(user, pass)` → adds an `Authorization: Basic …` header.
- `headers` → extra request headers.
- `verify_tls=False` → uses an unverified SSL context
  (`ssl._create_unverified_context()`), needed for the self-signed certs
  courtroom VC codecs commonly present on the LAN.
- **Return values are outcomes, not exceptions for HTTP status:** a 2xx returns
  `"HTTP 200 OK"`; a non-2xx the server *answered* (e.g. 401/404) returns
  `"HTTP 404 Not Found"` — because it still means "we reached the device". Only
  a genuine connection failure (`URLError`/`OSError`/`ValueError`) raises
  `ControlError`.

`http_get(url, timeout)` is a thin GET-only convenience wrapper.

### `tcp_send(...)`

The raw-TCP transport:

```python
tcp_send(host, port, payload: bytes, timeout=5.0, read_response=False) -> str
```

- Opens a socket with `socket.create_connection`, sends the payload.
- If `read_response=False` → returns `"Sent N bytes to host:port"`.
- If `read_response=True` → waits up to `timeout` for a reply, reads up to 2048
  bytes, and returns `"Reply: …"` (or a "no response / empty reply" note).
- A missing port or a connection failure raises `ControlError`.

### `ControlError`

The single typed exception for "a control command could not be completed". The
UI catches it and shows the message in the control panel — never a stack trace.

---

## 3. The config-driven command system (`controls.py`)

This is what makes mission-deck **vendor-neutral**: control buttons are
*described* in config rather than coded per vendor.

### `controls_for(device, browser_cfg) -> list[DeviceControl]`

Builds the ordered list of actions the control panel renders:

1. A built-in **"Open Web UI"** action (kind `"web"`) — if the device has a
   `web_url`. Clicking it calls `browser.open_urls([url], cfg)`.
2. One `DeviceControl` per valid entry in the device's `commands` array (kind
   `"command"`), built by `_make_command_control`.

### `DeviceControl`

```python
@dataclass(slots=True)
class DeviceControl:
    id: str
    label: str
    run: Callable[[str | None], str]   # run(value) -> result message
    prompt: str | None = None          # if set, UI asks for {value} first
    kind: str = "command"              # "command" | "web"
    source: dict | None = None         # the originating config entry (for "edit")
```

The `run` closure captures everything it needs and returns a human-readable
result string. Because each control carries its `source` dict, the UI can offer
an "edit this command" affordance for config-driven commands.

### Building a command (`_make_command_control`)

- Reads `id`/`label`/`protocol`/`prompt`.
- **HTTP/HTTPS:** requires a `url`; reads `method`, `body`, `auth`, `headers`,
  `verify_tls`, `timeout`; returns a `run` that substitutes placeholders and
  calls `http_request`.
- **TCP:** requires a `payload`; reads `port` (override) and `read_response`;
  returns a `run` that encodes the substituted payload and calls `tcp_send`.
- Anything else (or a malformed entry) → `None` (skipped).

### Placeholder substitution (`_format`)

`{host}`, `{port}`, and `{value}` are replaced in the URL, payload, and body at
the moment the command runs:

- `{host}` → `device.host`
- `{port}` → `str(device.port)`
- `{value}` → the prompted value, or `""` when there's no prompt

### Validation (`validate_command_spec`)

Used by the in-app **command editor** so operators get a plain-English reason
instead of a button that silently does nothing — e.g.:

- *"Give the command a label (the button text)."*
- *"An HTTP command needs a URL to request."*
- *"A TCP command needs a payload to send."*
- *"The port must be a whole number from 0 to 65535."*
- *"auth must be {"username": …, "password": …} or [username, password]."*

### Auth parsing (`_parse_auth`)

Accepts `{"username": …, "password": …}` (also honours `"user"`/`"pass"`) or a
two-item `[user, pass]` list; anything else yields `None`.

---

## 4. How a command executes (end to end)

1. Operator clicks a command button in `DeviceControlDialog`.
2. If the control has a `prompt`, the UI asks for a value.
3. The dialog calls `app.run_background(work, on_done)`:
   - `work` runs `control.run(value)` on a **daemon thread** (blocking I/O).
   - `on_done(result_or_error)` is marshalled back to the Tk thread to update the
     panel's status line — green for success, red for a `ControlError`.
4. A `device.command` **audit event** records the device, control label, and
   outcome.

The UI never blocks: a slow or dead device just yields an error in the panel
after the timeout.

---

## 5. Recorder polling (`fetch_recording_status`)

For `recorder` devices with a `recording_status_url`:

```python
fetch_recording_status(url, json_path="", timeout=5.0) -> RecordingStatus
```

- GETs the URL, parses JSON, and navigates `json_path` (dot-separated, e.g.
  `state.recording`); an empty path uses the whole body.
- `_parse_recording_value` interprets the value leniently:
  - bool / int → `RECORDING` if truthy, else `IDLE`;
  - strings: `recording`/`active`/`true`/`1`/`on`/`started`/`running` →
    `RECORDING`; `paused`/`pause`/`suspended` → `PAUSED`;
    `idle`/`stopped`/`false`/`0`/`off`/`inactive`/`ready` → `IDLE`;
  - anything else → `UNKNOWN`.
- **Any** network or parse error returns `UNKNOWN` — recorder polling never
  surfaces an error to the operator, it just shows "Unknown".

The app polls this on a background thread after a status check and updates the
recorder's card badge and control-panel status. Start/Stop buttons invoke
`recording_start_url` / `recording_stop_url` via the standard control path.

---

## 6. Opening web UIs (`browser.py`)

`open_urls(urls, cfg, in_thread=True)`:

1. Drops empty URLs; returns the count to be opened.
2. By default launches on a **daemon thread** so opening a dozen tabs never
   blocks the UI.
3. Resolution strategy in `_open_blocking`:
   - If an explicit executable is known (config `path`, or a friendly `name`
     resolved on `PATH`): `subprocess.Popen([exe, "--new-window"?, *urls])`.
     `--new-window` is added only for Chromium-family executables, opening all
     URLs as tabs in **one** new window.
   - Otherwise fall back to the `webbrowser` module: the first URL hints "new
     window" (`new=1`), the rest open as tabs (`new=2`).
4. A failed explicit launch logs a warning and falls back to the OS default
   rather than failing.

`BrowserConfig.from_settings` accepts a string (path *or* name, distinguished by
slashes/`.exe`) or an object (`path`/`name`/`new_window`).

---

## 7. Security notes for this layer

- **Probes are connect-and-close** — no data is sent during a status check, so a
  check can't accidentally trigger device behaviour.
- **`verify_tls: false` is opt-in per command** and exists specifically for
  self-signed LAN certs; prefer leaving TLS verification on where the device
  supports a valid cert.
- **Credentials** for `auth` come from the git-ignored `config.json` only.
- **All command outcomes are audited.**

See the [Security Model](Security-Model.md) for the full picture.
