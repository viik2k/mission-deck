# mission-deck wiki

This folder contains the project's long-form documentation. **Start at
[Home.md](Home.md)** — it's the index and navigation hub.

## Contents

| Article | For | Covers |
|---------|-----|--------|
| [Home](Home.md) | everyone | Index, audience map, at-a-glance facts. |
| [User Guide](User-Guide.md) | operators | Day-to-day use: welcome screen, the estate Overview, navigation, status checks, web UIs, device controls, settings. |
| [Installation & Setup](Installation-and-Setup.md) | admins | Running from source, building the EXE, deployment, env vars, upgrading. |
| [Configuration Reference](Configuration-Reference.md) | admins | The full `config.json` schema, device types, control commands, browser config, per-user state. |
| [Architecture Overview](Architecture-Overview.md) | devs | Module boundaries, data model, threading, the Overview/estate sweep, uptime history, startup flow, UI composition. |
| [Networking & Device Control](Networking-and-Device-Control.md) | devs | Status checks (monitor registry, bounded concurrency, estate sweep), HTTP/TCP transports, config-driven commands, recorder polling, browser launch. |
| [Security Model](Security-Model.md) | everyone | Logic/data separation, credentials, TLS, the audit trail, threat model. |
| [Logging & Auditing](Logging-and-Auditing.md) | admins | The two log streams, locations, rotation, event catalogue, crash capture. |
| [Maintenance & Troubleshooting](Maintenance-and-Troubleshooting.md) | admins | Common problems, housekeeping, upgrading, validation, escalation. |
| [Developer Guide](Developer-Guide.md) | devs | Conventions, adding device types/config fields/commands, threading contract, contributing. |
| [Glossary](Glossary.md) | everyone | Domain, AV, and mission-deck terms. |

## Conventions

- Articles are plain GitHub-flavoured Markdown and link to each other with
  relative paths, so they render correctly both in the repo and if published to
  a GitHub Wiki.
- Documentation uses **dummy data only** — never include real IPs, hostnames, or
  credentials (see [Security Model](Security-Model.md)).
- When you change behaviour in the code, update the matching article in the same
  PR.
