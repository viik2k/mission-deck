# Installation & Setup

This page covers running mission-deck from source, building the single-file
Windows executable, and deploying it to operator workstations. It is aimed at
**administrators**.

---

## 1. Requirements

| Requirement | Notes |
|-------------|-------|
| **Python 3.11+** | Required to run from source or build the EXE. End users of a packaged build do **not** need Python. |
| **OS** | Primarily Windows (the original deployment target), but the code runs on macOS and Linux too. Config/state/log paths follow each OS's conventions. |
| **Runtime dependencies** | `customtkinter` (modern dark UI) and `pillow` (image/icon rendering). |
| **No networking library** | Status checks and control transports use the standard-library `asyncio`/`socket`/`urllib` only — there is no third-party HTTP dependency. |
| **ffmpeg** *(optional)* | Only needed for the camera **Live View** pop-out (RTSP/RTMP decoding). Install it on `PATH`, place `ffmpeg.exe` next to `mission-deck.exe`, or set `MISSION_DECK_FFMPEG`. Everything else works without it. |

`requirements.txt`:

```
customtkinter>=5.2.2
pillow>=10.0.0
```

`requirements-dev.txt` adds `pyinstaller>=6.6` for building the EXE.

---

## 2. Running from source

```bash
# 1. Get the code
git clone <your-fork-url> mission-deck
cd mission-deck

# 2. Create and activate a virtual environment
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

# 3. Install runtime dependencies
pip install -r requirements.txt

# 4. Point the app at the bundled demo data so it doesn't prompt for a file
#    Windows (PowerShell):
$env:MISSION_DECK_CONFIG = "$PWD\config.example.json"
#    macOS / Linux:
export MISSION_DECK_CONFIG="$PWD/config.example.json"

# 5. Run
python -m mission_deck
```

The entry point is `mission_deck/__main__.py`, which calls `app.main()`.

> Omit step 4 to exercise the real config-discovery flow (and the welcome
> screen). See
> [Config discovery order](Configuration-Reference.md#config-discovery-order).

---

## 3. Building a single EXE

The app is designed to ship as **one double-clickable `.exe`** so non-technical
operators don't need a Python install.

```powershell
pip install -r requirements-dev.txt
pyinstaller mission-deck.spec
# Output: dist\mission-deck.exe   (~19 MB, windowed / no console)
```

### What the build spec does

`mission-deck.spec` is hand-maintained source (it is explicitly **un-ignored**
in `.gitignore`). Key points:

- **Bundles `config.example.json`** so the welcome screen's "Explore Demo Data"
  works in a fresh install.
- **Collects CustomTkinter's theme/asset JSON** automatically
  (`collect_data_files("customtkinter")`) — without this the packaged app fails
  to start.
- **`console=False`** → a windowed app with no console window.
- The user's **real `config.json` is *not* bundled** — it lives next to the EXE
  or in the per-user directory, and the chosen file is remembered between runs.

### Branding the EXE

Set an `.ico` path in the spec's `EXE(..., icon=...)` argument (currently
`icon=None`).

> The spec uses `upx=True`. UPX compression is optional; if UPX isn't installed,
> PyInstaller still builds, just without the size reduction.

---

## 4. Deploying to operator workstations

A typical rollout:

1. **Build** `dist\mission-deck.exe` on a build machine.
2. **Copy the EXE** to each operator workstation (or a shared launch location).
3. **Provide a real `config.json`** for the site. Recommended locations
   (first match wins at runtime):
   - Next to the EXE, or
   - In the per-user config directory:
     - **Windows:** `%APPDATA%\mission-deck\config.json`
     - **macOS:** `~/Library/Application Support/mission-deck/config.json`
     - **Linux:** `~/.config/mission-deck/config.json`
   - Or set the `MISSION_DECK_CONFIG` environment variable to an explicit path
     (handy for a locked-down or network-shared config).
4. **First run:** if no config is found, operators get the welcome screen and
   can open the site config once — it's remembered thereafter.

### Read-only install friendliness

The EXE can live in a read-only location (e.g. `Program Files`) because:

- **Per-user preferences** are written to `state.json` in the per-user config
  directory — never next to the EXE.
- **Logs** are written to a writable per-user `logs/` directory (see
  [Logging & Auditing](Logging-and-Auditing.md)). If even that directory can't
  be created, the app degrades gracefully to console logging rather than failing
  to start.

---

## 5. Configuration at install time

You must supply a real `config.json` with your site's rooms and devices. Start
from the shipped example:

```bash
cp config.example.json config.json
# then edit config.json with your real rooms, IPs, and devices
```

> 🔐 **`config.json` is git-ignored and must never be committed.** The repo
> ships only `config.example.json` (dummy data). See the
> [Security Model](Security-Model.md).

The full schema — app settings, rooms, devices, device types, control commands,
and browser configuration — is documented in the
[Configuration Reference](Configuration-Reference.md).

---

## 6. Environment variables

| Variable | Effect |
|----------|--------|
| `MISSION_DECK_CONFIG` | Absolute path to the config file to load (highest-priority discovery location). |
| `MISSION_DECK_LOG_DIR` | Directory for both log files (overrides the default per-user `logs/`). |
| `MISSION_DECK_LOG_LEVEL` | Diagnostic verbosity: `DEBUG`, `INFO` (default), `WARNING`, … Use `DEBUG` to trace individual probes and commands. |
| `MISSION_DECK_FFMPEG` | Absolute path to the ffmpeg binary used by the camera **Live View** (checked before an `ffmpeg.exe` next to the exe and `PATH`). |

On Windows the per-user base honours `%APPDATA%`; on Linux it honours
`XDG_CONFIG_HOME` (falling back to `~/.config`).

---

## 7. Upgrading

To upgrade an installation:

1. Replace `mission-deck.exe` (or pull the new source and re-run from source).
2. Leave the operator's `config.json` and `state.json` in place — they are
   independent of the binary.
3. If a release **bumps the schema version**, update your `config.json` to match
   (the app will refuse to load an unsupported `schema_version` with a clear
   error). The current supported version is **`1`**.

See [Maintenance & Troubleshooting](Maintenance-and-Troubleshooting.md) for
post-upgrade verification steps.

---

## 8. Verifying an install

There is no automated test suite yet; manual verification against the demo data
is the standard:

- The app launches and renders the dashboard against `config.example.json`.
- Switching rooms and expanding/collapsing city groups works.
- **Check Status** flips indicators (most demo IPs are unreachable, so expect
  red — that confirms the checker runs).
- **Open Web UIs** launches your configured browser.
- Loading a deliberately broken config produces a **clear error dialog**, not a
  stack trace.
