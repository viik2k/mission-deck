# Contributing to mission-deck

Thanks for helping improve mission-deck! This guide covers how to set up a dev
environment, the project's conventions, and how to extend it safely.

## 🔐 Golden rule: never commit environment data

mission-deck is open source and deliberately keeps **all** infrastructure data
(IP addresses, hostnames, device names) out of the repository.

- Real data lives only in a local `config.json`, which is **git-ignored**.
- The repo contains exactly one config: `config.example.json`, with **dummy
  data only**.
- Before every commit, double-check you are not adding real IPs/hostnames —
  even in tests, screenshots, issues, or PR descriptions.

If you find real environment data committed anywhere, treat it as a security
issue: report it privately rather than opening a public issue.

## Development setup

```bash
git clone <your-fork-url> mission-deck
cd mission-deck

python -m venv .venv
# Windows: .venv\Scripts\activate   |   Unix: source .venv/bin/activate
pip install -r requirements.txt
```

Run the app against the bundled dummy data:

```bash
# Point the app at the example so it doesn't prompt for a file:
#   Windows (PowerShell):  $env:MISSION_DECK_CONFIG = "$PWD\config.example.json"
#   Unix:                  export MISSION_DECK_CONFIG="$PWD/config.example.json"
python -m mission_deck
```

## Project layout & responsibilities

Each module has a single, well-defined job. Please keep these boundaries:

| Module | Responsibility | Keep out |
|--------|----------------|----------|
| `config.py` | Find, load, **structurally** validate the JSON | Device internals, GUI |
| `models.py` | Typed `Site` / `Room` / `Device` + device registry | File I/O, GUI, networking |
| `browser.py` | Open web UIs in the configured browser | GUI, models |
| `theme.py` | Colours + sizing tokens | Logic |
| `app.py` | CustomTkinter UI and event handling | Business rules that belong in models |

Network/status-check logic lives in `network.py` (the async checker) and must
run **off** the Tk UI thread, marshalling results back via the app's
`set_device_status` / `set_room_status` hooks.

## Code style

- **Python 3.11+**, full type hints, `from __future__ import annotations`.
- Data containers are `@dataclass(slots=True)`.
- Prefer small, pure functions and validation **at the edges** (raise
  `ConfigError` / `DeviceConfigError` with a precise, human-readable message).
- Match the surrounding code's naming and comment density. Comments explain
  *why*, not *what*.
- No new third-party dependencies without discussion — the runtime footprint is
  intentionally tiny (`customtkinter`, `pillow`).

## How to add a new device type

Device classes are registered, so adding one is a few lines in `models.py`:

```python
@register_device("projector", "beamer")   # config "type" values that map here
@dataclass(slots=True)
class Projector(Device):
    category: str = "Projector"            # the sidebar/card grouping label

    async def send_command(self, command: str, **kwargs):
        # Speak the device's real protocol here (HTTP/CGI, TCP, …).
        ...
```

That's it — the UI groups it automatically, and unknown types still fall back
to `GenericDevice`. If the type has a web UI, make sure `protocol`/`web_*`
fields resolve a `web_url` (see the README's config schema).

## How to add a config field

1. Parse and validate it in the relevant `from_dict` (`Device`/`Room`) or in
   `config.py` for top-level/app settings.
2. Unknown device keys are preserved in `Device.extra` automatically, so
   forward-compatible fields survive a load.
3. If the on-disk format changes incompatibly, bump `SUPPORTED_SCHEMA_VERSION`
   in `config.py` and update `config.example.json` + the README.
4. Always update `config.example.json` to document the new field with dummy
   data.

## Testing your change

There is no heavy test harness yet; at minimum, before opening a PR:

- The app launches and renders against `config.example.json`.
- Switching rooms and toggling city groups works.
- If you touched parsing, load a deliberately broken config and confirm you get
  a clear error (not a stack trace in a widget callback).
- If you touched performance-sensitive paths, sanity-check with a large
  generated config (the UI targets snappy room switching at 100+ rooms).

Contributions adding a proper `pytest` suite are very welcome.

## Commits & pull requests

- Branch off `main`; keep PRs focused.
- Write clear commit messages explaining the *why*.
- Describe what you changed and how you verified it in the PR.
- Confirm no real environment data is included.

## Reporting bugs / requesting features

Open an issue with:

- What you expected vs. what happened.
- Steps to reproduce (using **dummy** data).
- OS, Python version, and `customtkinter` version.

Thanks again for contributing! 🎛️
