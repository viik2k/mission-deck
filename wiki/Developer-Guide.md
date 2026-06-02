# Developer Guide

This page is for developers extending or modifying mission-deck. Read the
[Architecture Overview](Architecture-Overview.md) first for the big picture;
this guide covers conventions and the common "how do I add X?" tasks.

---

## 1. Dev environment

```bash
git clone <your-fork-url> mission-deck
cd mission-deck
python -m venv .venv
.venv\Scripts\activate          # Windows;  source .venv/bin/activate on Unix
pip install -r requirements.txt

# Run against the dummy data so you skip the welcome screen:
export MISSION_DECK_CONFIG="$PWD/config.example.json"   # $env:… on PowerShell
python -m mission_deck
```

There is **no automated test suite** yet. Manual verification against
`config.example.json` is the current standard (contributions adding a `pytest`
suite are welcome). There is also no configured linter/formatter, though a
`.ruff_cache/` entry in `.gitignore` suggests `ruff` has been used.

---

## 2. Code conventions

- **Python 3.11+**, `from __future__ import annotations`, **full type hints**.
- Data containers are `@dataclass(slots=True)`.
- **Validate at the edges.** Raise typed, human-readable exceptions
  (`ConfigError`, `DeviceConfigError`, `ControlError`) — never a bare
  `Exception`, and never a raw traceback into a widget callback.
- **Networking: stdlib only** (`asyncio`, `socket`, `ssl`, `urllib`). No new
  third-party dependencies beyond `customtkinter` and `pillow` without
  discussion — the tiny footprint is intentional.
- **Respect module boundaries** (see the table in the
  [Architecture Overview](Architecture-Overview.md#responsibilities-and-constraints)).
  Notably: `models.py` and `theme.py` stay **logging-free and I/O-free**; the UI
  thread is the only place widgets are touched.
- Comments explain **why**, not what. Match the surrounding density.

---

## 3. Adding a new device type

Device classes are registry-dispatched, so a new type is **one decorator** in
`models.py`:

```python
@register_device("projector", "beamer")   # the config `type` values that map here
@dataclass(slots=True)
class Projector(Device):
    category: str = "Projector"            # the card/sidebar grouping label
```

That's it:

- The UI groups it automatically by `category`.
- Unknown types still fall back to `GenericDevice`, so nothing breaks.
- Registering an already-used type name raises at import time (catches
  duplicate-alias mistakes).

If the type needs **per-class control logic**, override the async
`send_command` hook on the base `Device` — but note that most control is handled
generically by the **config-driven command system** (`controls.py`), so prefer
adding `commands` entries in config over bespoke code.

If the type has a **web UI**, ensure `protocol`/`web_*` resolve a `web_url` (see
the [Configuration Reference](Configuration-Reference.md#control-port-vs-web-ui--an-important-distinction)).

**Don't forget:** add the new type and its fields to `config.example.json` with
**dummy data**, and document it in the
[Configuration Reference](Configuration-Reference.md#device-types).

---

## 4. Adding a config field

1. **Parse + validate** it in the relevant `from_dict` (`Device.from_dict` /
   `Room.from_dict`) or, for top-level/app settings, in `config.py`.
2. **Serialise** it in the matching `to_dict` if it's a first-class field.
   - Or do nothing extra: **unknown device/room keys are preserved automatically**
     in `extra` and round-trip through `to_dict`. Many features (`web_*`,
     `commands`, recorder URLs) ride on `extra` rather than being declared
     fields.
3. If the on-disk format changes **incompatibly**, bump
   `SUPPORTED_SCHEMA_VERSION` in `config.py` and update `config.example.json` +
   the docs.
4. **Always** document the field with dummy data in `config.example.json`.

---

## 5. Adding a control command capability

Config-driven commands are built in `controls.py`:

- `controls_for(device, browser_cfg)` assembles the action list (built-in
  "Open Web UI" + one per `commands` entry).
- `_make_command_control(device, spec)` turns one spec into a `DeviceControl`
  whose `run(value)` closure does the I/O via `network.py`
  (`http_request`/`tcp_send`).
- `validate_command_spec(spec)` backs the in-app command editor with friendly
  error messages.

To add a new command capability (say, a new protocol or option):

1. Extend `_make_command_control` to parse the new keys and build the right
   `run` closure.
2. Add the matching transport to `network.py` if needed (keep it blocking,
   timeout-bounded, and raising `ControlError` on failure).
3. Extend `validate_command_spec` so the editor rejects bad input clearly.
4. Document the new keys in the
   [Configuration Reference](Configuration-Reference.md#control-commands) and add
   a dummy example to `config.example.json`.

---

## 6. The threading contract (don't break it)

- Anything doing **network or file I/O** runs on a worker/daemon thread —
  status checks via `network.run_status_checks`, commands and recorder polls via
  `app.run_background(work, on_done)`.
- Anything touching a **widget** must be on the **Tk thread**. Workers publish
  results to a thread-safe queue / pass them through `on_done`, and `app.after`
  marshals them back.
- Honour the **generation counter** when adding async result handling so stale
  results from a previous room/check are discarded, not applied to the wrong
  widgets.

See [Networking & Device Control](Networking-and-Device-Control.md) for the
mechanics.

---

## 7. UI architecture notes (`app.py`, `editors.py`)

- `App(ctk.CTk)` owns the window, the result queue, and all orchestration
  (sidebar build, room rendering, status checks, settings, config persistence,
  auto-refresh).
- **Widget pooling:** `DeviceCard` and `RoomButton` are pooled and rebound
  (`set_device()` / rebind) rather than recreated; `CityGroup` builds its room
  buttons lazily on first expand. This is the key to staying snappy at 100+
  rooms — preserve it when touching rendering code.
- **Dialogs:** `DeviceControlDialog` (controls), `SettingsDialog` (preferences),
  `WelcomeWindow` (first-run), and the `editors.py` form dialogs
  (`RoomEditorDialog`, `DeviceEditorDialog`, `CommandEditorDialog`). The editors
  go through `models`/`config` validation and write back with the atomic
  `save_config`.
- **Soft restart:** switching config sets `app.requested_config` and exits the
  mainloop; the `_run` loop re-enters discovery with the new path. Use this
  pattern rather than mutating a live `Site` in place when the whole config
  changes.

---

## 8. Logging & auditing in new code

```python
import logging
logger = logging.getLogger(__name__)          # diagnostic, per module

from mission_deck.logging_setup import audit
audit("device.command", device_id=dev.id, control=label, ok=True)   # compliance
```

- Use module loggers for diagnostics; keep `models.py`/`theme.py` logging-free
  (their typed exceptions are logged by callers).
- Add an `audit(...)` call for any **new operator action** (a state-changing
  thing a user does). Keep audit fields JSON-safe; `audit()` won't raise.

---

## 9. Contributing checklist

Before opening a PR:

- [ ] App launches and renders against `config.example.json`.
- [ ] Room switching and city-group toggles work.
- [ ] If you touched parsing: a deliberately broken config yields a **clear
      error dialog**, not a stack trace.
- [ ] If you touched a hot path: sanity-check with a large generated config
      (target: snappy switching at 100+ rooms).
- [ ] `config.example.json` documents any new field/type with **dummy data**.
- [ ] **No real environment data** anywhere — code, tests, screenshots, or PR
      text. (See the [Security Model](Security-Model.md).)
- [ ] Commit messages explain the *why*.

---

## 10. Project metadata

- Version lives in `mission_deck/__init__.py` (`__version__`).
- Build spec: `mission-deck.spec` (hand-maintained source; un-ignored in
  `.gitignore`).
- Entry point: `mission_deck/__main__.py` → `app.main()`.
