# Logging & Auditing

mission-deck keeps **two distinct log streams**, matching how an operations team
actually uses them: a **diagnostic** log for support/debugging and an **audit**
log for compliance. Both are configured once at startup by
`logging_setup.setup_logging()` and use the Python standard library only.

---

## 1. The two streams at a glance

| | Diagnostic | Audit |
|---|-----------|-------|
| **File** | `mission-deck.log` | `audit.log` |
| **Audience** | Developers / support | Compliance / operations |
| **Format** | Human-readable text lines | One JSON object per line |
| **Content** | Config loads, probes, transports, browser launches, full tracebacks for anything unexpected | *Who did what, to which device, when, with what outcome* |
| **Level** | `INFO` by default (configurable) | `INFO` only |
| **Logger** | `logging.getLogger(__name__)` per module | `logging_setup.audit(event, **fields)` |
| **Propagation** | Goes to console + diagnostic file | **Isolated** — never propagates to diagnostic handlers |

---

## 2. Where the logs live

Both files are written to a `logs/` subdirectory of the writable **per-user
config directory**:

- **Windows:** `%APPDATA%\mission-deck\logs\`
- **macOS:** `~/Library/Application Support/mission-deck/logs/`
- **Linux:** `~/.config/mission-deck/logs/` (honours `XDG_CONFIG_HOME`)

Writing to a per-user location (never next to the binary) is what lets the app
ship as a **read-only single EXE** in `Program Files` without trouble.

### If the log directory can't be created

The app degrades gracefully: it keeps **console logging** and gives up on files,
rather than failing to start. (In a windowed PyInstaller build there's no
console — `sys.stderr is None` — so the console handler is simply skipped.)

---

## 3. Diagnostic log (`mission-deck.log`)

The developer/support view. Every module logs via the standard
`logging.getLogger(__name__)` pattern, so lines are tagged with their source
module and thread:

```
2026-06-02 09:15:04,321 INFO     [MainThread] mission_deck.config: Loaded config /…/config.json (4 room(s))
2026-06-02 09:15:11,840 DEBUG    [Thread-3]   mission_deck.network: Probe 1a-dsp (10.10.1.30:22) timed out after 2.0s
2026-06-02 09:15:12,002 INFO     [Thread-5]   mission_deck.network: HTTP POST https://…/putxml -> 200
```

Format: `%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s`.

What lands here includes config discovery/load/save, individual device probes
(at `DEBUG`), HTTP/TCP control transports, browser launches, and **full
tracebacks** for anything unexpected — including crashes on background threads
(see [§6 Crash capture](#6-crash-capture)).

### Adjusting verbosity

Set `MISSION_DECK_LOG_LEVEL` to `DEBUG`, `INFO` (default), `WARNING`, etc. Use
`DEBUG` to trace individual probes and command transports when chasing a bug.
The root logger is set to `DEBUG` and each *handler* decides what it actually
emits, so raising the level surfaces more without code changes.

---

## 4. Audit log (`audit.log`)

The compliance/operations view: an append-only, machine-readable record of
operator actions. One JSON object per line. Every record carries:

- `ts` — ISO-8601 UTC timestamp, millisecond precision.
- `event` — a short dotted action name.
- `user` — the OS username (`getpass.getuser()`, or `"unknown"`).

…plus event-specific fields. Example lines:

```json
{"ts":"2026-06-02T09:15:04.321+00:00","event":"app.start","user":"avtech","version":"0.2.0","log_dir":"…/logs"}
{"ts":"2026-06-02T09:15:04.402+00:00","event":"config.load","user":"avtech","path":"…/config.json","demo":false,"rooms":4,"ok":true}
{"ts":"2026-06-02T09:16:20.118+00:00","event":"status_check.complete","user":"avtech","room_id":"courtroom-1a","room_name":"Courtroom 1A","devices":6,"online":4,"offline":2}
{"ts":"2026-06-02T09:17:55.640+00:00","event":"room.open_web_uis","user":"avtech","room_id":"courtroom-1a","room_name":"Courtroom 1A","count":4,"browser":"chrome"}
{"ts":"2026-06-02T09:18:31.207+00:00","event":"device.command","user":"avtech","device_id":"3c-cisco-webex","device_name":"Cisco Webex Room Codec","device_type":"cisco_webex","host":"10.30.3.5","control":"Hang Up"}
```

### Event catalogue

| Event | When | Key fields |
|-------|------|-----------|
| `app.start` | App launches | `version`, `log_dir` |
| `app.stop` | App exits | — |
| `config.load` | A config is loaded (success or failure) | `path`, `demo`, `rooms`, `ok`, `error` |
| `config.save` | A config is written from the editors | `path`, `ok`, `rooms`, `error` |
| `config.switch` | Operator switches config | `frm`, `to` |
| `cloud.sync` | A remote config is pulled from the Cloud Sync view | `url`, `ok`, `error` |
| `settings.change` | Settings dialog saved | `ping_timeout_seconds`, `auto_refresh_seconds`, `browser_path`, `browser_new_window`, `start_on_dashboard`, `dashboard_poll_enabled`, `dashboard_poll_seconds`, `max_concurrent_checks` |
| `settings.change` | A plugin is enabled/disabled on the Plugins screen | `plugin`, `enabled` |
| `device.command` | A control command is issued | `device_id`, `device_name`, `device_type`, `host`, `control`, (outcome) |
| `room.open_web_uis` | Open Web UIs invoked | `room_id`, `room_name`, `count`, `browser` |
| `status_check.complete` | A room status check finishes | `room_id`, `room_name`, `devices`, `online`, `offline` |
| `status_check.estate` | An estate-wide sweep finishes | `devices`, `online`, `offline` |
| `report.export` | A CSV status report is exported | `path` |
| `stream.open` | A camera Live View feed is opened (or switched to) | `device_id`, `device_name`, `room`, `url` (credential-redacted) |
| `stream.drop` | A live feed stalled/ended and reconnection began | `device_id`, `device_name`, `drops`, `error` |
| `stream.close` | The Live View feed is closed or switched away | `device_id`, `device_name`, `reason`, `frames`, `drops`, `duration_seconds` |
| `stream.snapshot` | A Live View frame is saved as a PNG | `device_id`, `device_name`, `path`, `ok` |

### Emitting audit events (developers)

```python
from mission_deck.logging_setup import audit
audit("device.command", device_id=dev.id, host=dev.host, control=label, ok=True)
```

`audit()` coerces non-JSON-safe values to strings and **never raises** into the
caller — auditing is best-effort. A failure to write an audit line is itself
logged to the diagnostic stream.

---

## 5. Rotation & disk safety

Both files use a `RotatingFileHandler`:

- **Rotate at ~2 MB**, keeping **5** backups (`mission-deck.log.1` …).
- This caps total disk use at a few tens of MB even on a long-lived install, so
  the logs can never run a workstation out of space.

---

## 6. Crash capture

`setup_logging()` installs excepthooks so **no crash is silently lost** — vital
in a windowed build where stderr goes nowhere:

- `sys.excepthook` → uncaught **main-thread** exceptions are logged `CRITICAL`
  with a full traceback (`KeyboardInterrupt` is passed through normally).
- `threading.excepthook` → uncaught **worker-thread** exceptions are logged
  `ERROR` with the thread name and traceback (`SystemExit` is ignored).

The top-level `main()` additionally wraps the whole run in a last-resort
`try/except` that logs the fatal error and shows a dialog before re-raising.

---

## 7. Configuration summary

| Variable | Effect | Default |
|----------|--------|---------|
| `MISSION_DECK_LOG_DIR` | Directory for **both** logs | per-user `…/mission-deck/logs` |
| `MISSION_DECK_LOG_LEVEL` | Diagnostic level (`DEBUG`/`INFO`/…) | `INFO` |

`setup_logging()` is **idempotent** — calling it again (e.g. on a soft-restart
that re-enters `main`) is a no-op and won't double-add handlers.

---

## 8. Operational recommendations

- **Forward the audit log to a central collector / SIEM.** The local file is
  append-only by convention but not tamper-proof; shipping it off-box gives a
  durable, access-controlled record. (See the [Security Model](Security-Model.md).)
- **Collect `mission-deck.log` with support tickets.** Ask operators to
  reproduce with `MISSION_DECK_LOG_LEVEL=DEBUG` for hard-to-diagnose issues.
- **Don't rely on the diagnostic log for compliance** — that's the audit log's
  job; the two are deliberately separate.
- **Mind privacy in the diagnostic log:** at `DEBUG` it records hosts/ports and
  request URLs. Treat it as sensitive and clean it up per your retention policy.
