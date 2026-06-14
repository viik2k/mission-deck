# mission-deck Wiki

Welcome to the **mission-deck** wiki — the complete reference for operators,
administrators, and developers of the courtroom AV management dashboard.

> **mission-deck** is a Python/CustomTkinter desktop application that gives AV
> technicians a single dark-mode dashboard to browse every courtroom's
> equipment, check whether devices are online, watch estate-wide health and
> uptime at a glance, open every device's web UI in one click, and issue
> vendor-neutral control commands — all driven by an external, git-ignored
> `config.json`.

---

## Who this wiki is for

mission-deck serves three audiences, and this wiki is organised around them:

| You are… | Start here |
|----------|-----------|
| **An AV technician / operator** using the app day to day | [User Guide](User-Guide.md) |
| **An administrator** installing, configuring, packaging, or maintaining the app | [Installation & Setup](Installation-and-Setup.md), [Configuration Reference](Configuration-Reference.md), [Maintenance & Troubleshooting](Maintenance-and-Troubleshooting.md) |
| **A developer** extending or modifying the codebase | [Architecture Overview](Architecture-Overview.md), [Developer Guide](Developer-Guide.md) |

---

## Article index

### For operators
- **[User Guide](User-Guide.md)** — launching the app, the welcome screen, the
  top-nav shell, the estate **Overview**, composable **Dashboards**, the
  **Plugins** screen (Cloud Sync + Activity Log), navigating cities/rooms/devices,
  checking status, opening web UIs, issuing device commands, recording controls,
  and the Settings dialog.

### For administrators
- **[Installation & Setup](Installation-and-Setup.md)** — running from source,
  building the single-file EXE, and deploying to operator workstations.
- **[Configuration Reference](Configuration-Reference.md)** — the complete
  `config.json` schema: app settings, rooms, devices, device types, control
  commands, web-UI resolution, and browser configuration.
- **[Logging & Auditing](Logging-and-Auditing.md)** — the diagnostic and audit
  log streams, where they live, how they rotate, and what every audit event
  means.
- **[Maintenance & Troubleshooting](Maintenance-and-Troubleshooting.md)** —
  common problems, diagnostics, upgrading, and operational housekeeping.

### For developers
- **[Architecture Overview](Architecture-Overview.md)** — module boundaries,
  the data-model hierarchy, the threading model, and config discovery.
- **[Networking & Device Control](Networking-and-Device-Control.md)** — how
  status checks (the pluggable monitor registry, bounded concurrency, the
  estate-wide sweep), control transports, and recording polling actually work.
- **[Developer Guide](Developer-Guide.md)** — code conventions, adding a device
  type, adding a config field, the UI architecture, and widget pooling.

### Cross-cutting
- **[Security Model](Security-Model.md)** — the "security by design" data
  separation, credential handling, TLS behaviour, the audit trail, and the
  threat model.
- **[Glossary](Glossary.md)** — domain and AV terminology used throughout.

---

## At a glance

| Property | Value |
|----------|-------|
| Language | Python 3.11+ |
| UI toolkit | CustomTkinter (dark mode) |
| Runtime dependencies | `customtkinter`, `pillow` (networking + history use stdlib only) |
| Packaging | PyInstaller → single windowed `.exe` |
| Config format | External `config.json`, `schema_version: 1` |
| Config location | git-ignored; repo ships `config.example.json` (dummy data only) |
| Uptime history | Best-effort SQLite `history.db` in the per-user dir |
| Test suite | None yet — manual verification against `config.example.json` |
| Current version | `0.2.0` (see `mission_deck/__init__.py`) |

---

## The one rule that matters most

> 🔐 **Never commit real environment data.**
> IP addresses, hostnames, and device names live **only** in a local,
> git-ignored `config.json`. The repository contains exactly one config file,
> `config.example.json`, and it holds dummy data only. See the
> [Security Model](Security-Model.md) for why this separation exists and how it
> is enforced.
