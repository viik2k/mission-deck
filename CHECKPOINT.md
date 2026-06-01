# CHECKPOINT — 2026-06-01 (before bed)

## What the Claude agents were doing

Adding **in-app config editing** — turning mission-deck from a read-only viewer into a full CRUD tool for rooms, devices, and control commands. No more editing `config.json` by hand.

**6 files modified + 1 new file.** All work is uncommitted on `main`.

### State of the branch

```
On branch main, up to date with origin/main
6 modified files (~630 LOC added)
1 new file:   mission_deck/editors.py (680 LOC)
Commits:      Initial -> license -> scaffold -> "Add device controls, settings, welcome & state"
```

---

## What was built (uncommitted)

### New module: `mission_deck/editors.py` — 680 lines
Three config-editing dialogs that let non-technical users manage the equipment estate without touching JSON:

| Dialog | What it does |
|--------|-------------|
| `RoomEditorDialog` | Add / edit / duplicate / delete rooms |
| `DeviceEditorDialog` | Add / edit / duplicate / delete devices in a room |
| `CommandEditorDialog` | Add / edit / delete control commands on a device (HTTP/TCP buttons) |

All three:
- Use `_FormDialog` as a shared scrollable-form base class
- Validate through the same code paths as the JSON loader (`Device.from_dict`, `validate_command_spec`)
- Trigger `app.persist_config()` → writes to disk via new `config.save_config()`
- Then tell the app to refresh the UI (`refresh_sidebar`, `refresh_current_room`)

### `config.py` — new function
- **`save_config(path, data)`** — atomically writes validated JSON (tmp file + `os.replace`)

### `models.py` — serialization + room health + recorder support
- **`Device.to_dict()`** — round-trip serialization (inverse of `from_dict`); preserves `extra` keys
- **`Room.to_dict()`** — serializes room + its devices back to JSON
- **`Site.to_dict()`** — serializes the whole site (rooms + app settings)
- **`Room.room_health`** — aggregate status property (ONLINE/OFFLINE/CHECKING/UNKNOWN across all devices)
- **`Room.extra`** field — forward-compatible unknown room-level keys
- **`Recorder` device type enhancements:**
  - `recording_status` field (RecordingStatus enum: UNKNOWN/IDLE/RECORDING/PAUSED)
  - `recording_status_url`, `recording_start_url`, `recording_stop_url` properties from `extra`
  - `RecordingStatus` enum in `models.py`

### `network.py` — recording status polling
- **`fetch_recording_status(url, json_path, timeout)`** — HTTP GET → JSON parse → navigate dot-path → return RecordingStatus
- **`_parse_recording_value(value)`** — converts bool/int/string to RecordingStatus

### `controls.py` — command editing support
- **`validate_command_spec(spec)`** — validates one `commands` entry, used by `CommandEditorDialog`
- **`DeviceControl.source`** field — back-reference to the originating config dict, enabling the "edit this command" pencil button

### `theme.py` — new palette entries
- `COLORS["online"]` (green), `COLORS["offline"]` (red) — for success/error feedback
- `recording_status_color()` / `recording_status_label()` — colors and labels for recorder status

### `app.py` — the big one (most of the ~427 line diff)

**Sidebar changes:**
- `RoomButton` now has a **health dot** (colored ●) reflecting `room.room_health`
- Plus/minus health dots handle hover tracking to avoid canvas square artifact
- "ROOMS" header now includes a **"＋ Add" button** (calls `add_room()`)
- `_build_sidebar()` is now idempotent (destroys old sidebar before rebuild)
- `refresh_sidebar()` — full rebuild + restore selection
- `_select_in_sidebar()` — expands collapsed city groups to find a room
- `refresh_current_room()` — re-renders after device changes
- `select_first_or_empty()` — fallback after deleting last room
- `_refresh_config_footer()` — updates "config: foo.json" label after save-as
- `_refresh_room_health()` — updates sidebar dot after status checks

**Room view changes:**
- **Empty-room placeholder** (pooled `_get_empty_hint()` widget) — shown when room has 0 devices, with "＋ Add a device" button
- Room-scoped action buttons (`Open Web UIs`, `Check Status`, `＋ Device`, `✎`) are **enabled/disabled** based on whether a room is selected
- `_show_empty_state()` now disables all room actions and clears cards
- `_render_room()` hides/shows empty hint as appropriate

**Device control dialog changes:**
- Grew from 360→540 px to accommodate editing bar
- `_build_actions()` — rebuilds control buttons; each config-driven button gets an **edit (✎) pencil** button
- **"＋ Add Command"** button at bottom of controls list
- **Editing bar** at bottom: "Edit Device" and "Remove" buttons
- `_set_status()` / `_clear_status()` — auto-clearing status messages
- Dialog re-reads device from model so edits are reflected live

**Config editing actions on App:**
- `persist_config()` — writes site to disk; if no config path (demo data), prompts "save as" first
- `add_room()` / `edit_current_room()` / `add_device()` — launch editor dialogs
- `switch_config_dialog()` updated to use `_refresh_config_footer()`

**Settings dialog changes:**
- Removed the appearance toggle (dark/light/system) — was causing issues

**Auto-refresh & status bar:**
- `_set_statusbar(text, duration_ms)` — temporary status messages that auto-clear
- Several callsites migrated from raw `_statusbar.configure()` to `_set_statusbar()`

---

## What's NOT done / known gaps / TODOs

### Recorder recording status — DONE ✅ (wired into the GUI on 2026-06-01)
- `fetch_recording_status` exists and works
- `Recorder.recording_status` field exists
- `RecordingStatus` enum and theme colors/labels exist
- **DONE:** `Recorder.recording_status_json_path` property (reads `extra`) so the
  status URL's JSON response can be navigated (e.g. `state.recording`)
- **DONE:** `DeviceCard` now shows a live recording pill (e.g. red "● Recording")
  for Recorder devices; hidden for every other device type
- **DONE:** the polling loop — `App._poll_recording()` fetches recording state
  off-thread for online recorders; triggered from `_apply_result` during every
  status check (manual + auto-refresh). Offline recorders reset to UNKNOWN.
- **DONE:** `DeviceControlDialog` now renders a "Recording" section with
  Start/Stop buttons (driven by `recording_start_url` / `recording_stop_url`);
  pressing one fires the URL and re-polls the live state.
- `config.example.json`'s recorder now carries dummy recording URLs so the
  feature is demonstrable out of the box.

### No tests
- The repo has `ruff` cache gitignored but no linter config, no test framework
- Manual verification only (`config.example.json`)

### Appearance toggle removed
- Was in Settings dialog, removed because it was causing issues. May need to be re-added properly later.

### Editors module integrated but untested E2E
- `editors.py` is imported from `app.py` but has never been run through a full add/edit/save/refresh cycle as far as we know
- The `TYPE_CHECKING` guard on `app.py` import in `editors.py` is suspicious — verify it doesn't cause runtime `NameError`

### CRLF warnings
- Git is warning about LF→CRLF conversion on all modified files (Windows platform). Clean the line endings before committing.

---

## Next steps for anyone picking this up

1. **Run and smoke-test the editing flow:**
   ```powershell
   $env:MISSION_DECK_CONFIG = "$PWD\config.example.json"
   python -m mission_deck
   ```
   Then: Add a room → Add a device → Edit the device → Add a command → Save → Check config.json was written.

2. **Wire up recorder recording status in the GUI** (the models and network functions are ready).

3. **Test the atomic save** — kill the process mid-save to verify no corruption.

4. **Clean line endings** before committing: `git add --renormalize .`

5. **Commit** with a message like: `Add in-app config editing (rooms, devices, commands) + recorder status support`

---

## Quick reference: commands

```powershell
# Run
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
$env:MISSION_DECK_CONFIG = "$PWD\config.example.json"
python -m mission_deck

# Build EXE
pip install -r requirements-dev.txt
pyinstaller mission-deck.spec
```

## Key files to read

| File | LOC | Purpose |
|------|-----|---------|
| `mission_deck/app.py` | ~1700 now | GUI + orchestration |
| `mission_deck/editors.py` | 680 | CRUD dialogs (NEW) |
| `mission_deck/models.py` | ~550 now | Data models + registry |
| `mission_deck/config.py` | ~280 now | Load + now save configs |
| `mission_deck/controls.py` | ~140 now | Device control actions |
| `mission_deck/network.py` | ~200 now | TCP/HTTP I/O + recording status fetch |
| `mission_deck/theme.py` | ~85 now | Colors + sizing |
| `CLAUDE.md` / `AGENTS.md` | — | Agent instructions (both exist, kept in sync) |
