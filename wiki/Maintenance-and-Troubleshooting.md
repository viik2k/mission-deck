# Maintenance & Troubleshooting

This page is for **administrators** keeping mission-deck running: diagnosing
problems, routine housekeeping, and upgrading. For first-time setup see
[Installation & Setup](Installation-and-Setup.md); for the config format see the
[Configuration Reference](Configuration-Reference.md).

---

## 1. First diagnostic step: the logs

Almost every problem is explained by the logs. They live in the per-user
`logs/` directory:

- **Windows:** `%APPDATA%\mission-deck\logs\`
- **macOS:** `~/Library/Application Support/mission-deck/logs/`
- **Linux:** `~/.config/mission-deck/logs/`

- `mission-deck.log` — diagnostic detail (config loads, probes, transports,
  tracebacks).
- `audit.log` — record of operator actions.

For a stubborn issue, reproduce it with verbose logging:

```bash
# Windows (PowerShell)
$env:MISSION_DECK_LOG_LEVEL = "DEBUG"
# macOS / Linux
export MISSION_DECK_LOG_LEVEL=DEBUG
```

See [Logging & Auditing](Logging-and-Auditing.md) for full details.

---

## 2. Common problems

### The app won't start / config won't load

The startup loop shows a **clear error dialog** and audits `config.load
ok=false`. Typical causes:

| Message hints at | Cause | Fix |
|------------------|-------|-----|
| "not valid JSON" (line/column) | A syntax error in `config.json` | Fix the JSON; validate with any linter. The message gives the line/column. |
| "missing required 'schema_version'" / "unsupported schema_version N" | Wrong/absent version | Set `"schema_version": 1`. |
| "'rooms' must be a list" / "rooms[i] must be a JSON object" | Malformed structure | Correct the shape per the [reference](Configuration-Reference.md#top-level-structure). |
| "missing or empty required string field 'host'" (etc.) | A device/room is missing a required field | Add the missing `id`/`name`/`type`/`host`. |
| "duplicate device id" / "duplicate room id" | Non-unique ids | Make ids unique (device ids unique within a room; room ids unique globally). |

After a load failure the app **clears the remembered path** and returns to the
welcome screen, so it won't spin on a bad file — you can open a different config.

### A device shows Offline but is actually up

A green dot only means "a TCP connection to the configured port succeeded".
Causes of a false Offline:

- **Timeout too short** — raise *Status-check timeout* in Settings (or
  `app.ping_timeout_seconds`). Slow or distant devices may need >2s.
- **Wrong port/protocol** — the status check uses `protocol`/`port`. Make sure
  they point at a port the device actually listens on.
- **No port to check** — a raw-`tcp` device with `port: 0` shows **Unknown**;
  give it a real port.
- **Firewall / network segmentation** — the workstation may not have a route to
  the device's port.

### Open Web UIs does nothing

- The room may have **no web-accessible devices** (SSH-only / raw-TCP devices
  are skipped by design).
- A configured **browser path is invalid** — the app logs a warning and falls
  back to the OS default. Check `browser.path` / the Settings *Browser* value.
- Check `mission-deck.log` for an `Opened N URL(s) via …` line to confirm what
  happened.

### A device has no "Open Web UI" button

It has no resolvable `web_url`. Add one of: an explicit `web_url`, or a web
scheme via `web_protocol`/`web_port` (+ optional `web_path`), or set the
device's `protocol` to `http`/`https`. See
[web-URL resolution](Configuration-Reference.md#control-port-vs-web-ui--an-important-distinction).

### A control command fails

The control panel shows the exact error. Common cases:

- **"Connection failed"** — device unreachable or wrong host/port.
- **An HTTP status like 401/403** — wrong/missing `auth` credentials.
- **TLS errors** — the device uses a self-signed cert; set `verify_tls: false`
  on that command (only for trusted LAN devices).
- **Nothing happens / no button** — the command spec is malformed; the in-app
  command editor validates and explains (e.g. "An HTTP command needs a URL").

### Recorder status always shows Unknown

`fetch_recording_status` returns Unknown on *any* network or parse error. Check:

- `recording_status_url` is reachable and returns JSON.
- `recording_status_json_path` matches the response shape (e.g.
  `state.recording`).
- TLS / auth (if the endpoint needs it).

### The Overview shows "—" for uptime (no history)

Uptime is built from saved samples. A room shows **—** until at least one
**Check Status** or **Refresh All** sweep has recorded a sample for it. If it
*never* fills in, the history database may be unwritable — check
`mission-deck.log` for a "Could not open/record history" warning. The database
lives at `<per-user dir>/history.db` (override with `MISSION_DECK_HISTORY_DB`);
uptime history is best-effort and never blocks the app.

### Settings or "last config" not remembered

Preferences persist to `state.json` in the per-user dir. If they don't stick,
that directory may be unwritable — check `mission-deck.log` for a
"Could not save state" warning and fix the directory permissions.

### Packaged EXE fails to launch

Almost always a packaging issue. Confirm the build used `mission-deck.spec`
(which collects CustomTkinter's assets and bundles `config.example.json`).
Rebuild with `pyinstaller mission-deck.spec`. See
[Installation & Setup](Installation-and-Setup.md#3-building-a-single-exe).

---

## 3. Routine housekeeping

- **Logs** self-manage: they rotate at ~2 MB with 5 backups, so no manual
  pruning is needed for disk space. For compliance, **forward `audit.log`** to a
  central collector.
- **Uptime history** (`history.db`) self-prunes: samples older than
  `history_retention_days` (default 30) are deleted on each launch. To reset
  trends, stop the app and delete the file — it's rebuilt on the next sweep.
- **Config backups:** keep `config.json` under your own (private, access-
  controlled) backup — it is *not* in git by design. Saves are atomic, so an
  in-app edit can't corrupt it mid-write, but a backup protects against mistakes.
- **Credential rotation:** when device passwords change, update the relevant
  `auth` blocks in `config.json`. See the
  [Security Model](Security-Model.md#credentials-in-config).

---

## 4. Upgrading

1. Replace the EXE (or pull new source).
2. **Leave `config.json` and `state.json` in place** — they're independent of the
   binary.
3. If the release **bumps the schema version**, the app will refuse an
   unsupported `schema_version` with a clear error; update `config.json` to the
   new version per the release notes.
4. **Post-upgrade smoke test** (no automated suite exists yet):
   - App launches and renders against the real config.
   - Switching rooms and toggling city groups works.
   - **Check Status** flips indicators.
   - **Open Web UIs** launches the browser.
   - A representative control command succeeds.
   - Loading a deliberately broken config produces a clean error dialog.

---

## 5. Validating a config before deploying

There's no separate CLI validator, but you can dry-run a config by pointing the
app at it:

```bash
# Windows (PowerShell)
$env:MISSION_DECK_CONFIG = "C:\path\to\candidate-config.json"
python -m mission_deck
```

A structural or content error appears as a dialog (and an audited
`config.load ok=false`) immediately on launch. Test against the demo data first
with `config.example.json` to confirm the app itself is healthy.

---

## 6. Health-check matrix

| Check | Expected result |
|-------|-----------------|
| Launch with valid config | Opens to the Overview (or last/first room per settings) |
| Open the ⌂ Overview, click Refresh All | KPIs/attention/uptime panels populate after the sweep |
| Launch with no config | Welcome screen |
| Launch with broken config | Error dialog, then welcome screen |
| Check Status on a reachable device | Green dot + latency |
| Check Status on an unreachable device | Red dot after the timeout |
| Open Web UIs | Browser opens one window with the room's web devices |
| Issue a command | Result/Reply or a clear error in the control panel |
| Edit + save a device | `config.json` updated atomically; `config.save ok=true` audited |

---

## 7. Escalation: what to collect for a bug report

- The exact **error dialog** text.
- `mission-deck.log` (ideally reproduced at `DEBUG`).
- OS, Python version, and `customtkinter` version.
- A **dummy-data** repro (never paste real IPs/credentials — see the
  [Security Model](Security-Model.md)).
