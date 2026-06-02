# Security Model

mission-deck manages **courtroom AV infrastructure** — a sensitive environment
where knowing the network layout, device addresses, and control credentials has
real value to an attacker. Security is therefore a first-class design concern,
not an afterthought. This page explains the protections built into the app, why
they exist, and the threat model they address.

---

## 1. Security by design: logic / data separation

> **The single most important property: no environment data lives in the
> repository.**

Application *logic* (the Python code) is strictly decoupled from environment
*data* (IP addresses, hostnames, device names, credentials):

- All site-specific data is loaded **at runtime** from a local `config.json`.
- `config.json` is **git-ignored** and never committed.
- The repository ships exactly one config — `config.example.json` — containing
  **fictional dummy data** (RFC-1918 example IPs, made-up courtrooms).

### Why this matters

- The open-source code can be shared, forked, and reviewed **without leaking**
  any real infrastructure.
- A leaked or public repo exposes *zero* operational data.
- Bug reports, screenshots, and PRs can use the demo data, so sensitive details
  never end up in issue trackers.

### How it's enforced

`.gitignore` blocks the real data while allowing the example:

```gitignore
config.json
config.*.json
!config.example.json
*.local.json
```

Plus a documented golden rule across `README.md`, `CONTRIBUTING.md`, and
`CLAUDE.md`: *never commit real environment data — treat any committed real IPs
as a security incident and report privately.*

> **Reviewer checklist:** before every commit, confirm no real IPs/hostnames
> appear in code, tests, the example config, screenshots, or PR text.

---

## 2. Credentials in config

Some control commands (notably VC codec APIs like Cisco xCommand and Poly REST)
require authentication. These credentials:

- Are supplied via a command's `auth` field
  (`{"username": …, "password": …}`), turned into an HTTP Basic header at
  request time.
- Live **only in the git-ignored `config.json`**, never in
  `config.example.json` (the example uses obvious placeholders like
  `"changeme"`).

### Operational guidance

- Treat `config.json` as a **secret-bearing file**. Restrict its filesystem
  permissions to the operator account(s) that need it.
- Prefer **least-privilege device accounts** for any credentials stored here.
- Consider a per-machine or network-shared, access-controlled config delivered
  via `MISSION_DECK_CONFIG` rather than copying secrets to every workstation.
- Rotate device credentials on the normal schedule; update `config.json`
  accordingly.

> **Known limitation:** credentials in `config.json` are stored in **plaintext
> JSON**. mission-deck does not currently encrypt them at rest. Protect the file
> with OS-level permissions, and avoid putting high-value credentials in it where
> a less-privileged alternative exists.

---

## 3. TLS behaviour

The HTTP control transport verifies TLS certificates **by default**
(`verify_tls=True`). Verification is only disabled when a command **explicitly**
opts out with `verify_tls: false`.

- This opt-out exists for a real-world reason: courtroom VC codecs and AV
  appliances on an isolated LAN frequently present **self-signed certificates**,
  which would otherwise make their APIs unusable.
- Because it's **per-command** and **off by default**, the insecure path is a
  deliberate, auditable choice rather than a blanket setting.

**Guidance:** leave verification on wherever the device supports a valid
certificate; reserve `verify_tls: false` for trusted devices on a controlled
network segment.

---

## 4. The audit trail

Every operator action is recorded to an append-only **audit log**
(`audit.log`), one JSON object per line. This is the compliance/operations view:
*who did what, to which device, when, and with what outcome.*

Audited events:

| Event | Recorded fields (besides `ts`, `event`, `user`) |
|-------|------------------------------------------------|
| `app.start` | version, log dir |
| `app.stop` | — |
| `config.load` | path, demo flag, room count, ok/error |
| `config.save` | path, ok/error, room count |
| `config.switch` | from/to paths |
| `settings.change` | timeout, auto-refresh, browser settings |
| `device.command` | device id/name/type, host, control label, (and outcome) |
| `room.open_web_uis` | room id/name, URL count, browser used |
| `status_check.complete` | room id/name, device/online/offline counts |
| `status_check.estate` | device/online/offline counts for an estate-wide sweep |

Every line carries an ISO-8601 UTC timestamp (`ts`), the dotted event name, and
the OS `user` who performed it. The audit logger **never propagates** to the
diagnostic handlers, so the two streams stay clean and separate.

### Why a separate, structured audit log

- AV techs operate equipment in a legal setting; an append-only record of every
  command (e.g. who started/stopped a recording, who dialled a call) is the
  whole point.
- JSON-per-line is trivially machine-parseable for ingestion into a SIEM or
  compliance pipeline.
- Auditing is **best-effort and never raises** into the caller's control flow —
  a logging failure can't break the operator's action, but a failure is itself
  logged to the diagnostic stream.

> The audit log is *append-only by convention*, written via a rotating file
> handler. It is **not cryptographically tamper-proof**. For stronger
> guarantees, ship the log to an external, access-controlled collector (see
> [Logging & Auditing](Logging-and-Auditing.md)).

See [Logging & Auditing](Logging-and-Auditing.md) for formats, locations, and
rotation.

---

## 5. Network behaviour & blast radius

mission-deck is intentionally conservative on the wire:

- **Status checks are read-only and side-effect-free.** The default `tcp` monitor
  opens a TCP connection and immediately closes it — **no payload is sent**. The
  optional `http`/`https` monitor issues a single GET to a health/web URL and
  reads the response; neither changes device state.
- **Control commands do exactly what config says** — a specific HTTP request or
  TCP payload to a specific device. There is no broadcast, scan, or discovery.
- **Standard library only** for networking (`asyncio`, `socket`, `ssl`,
  `urllib`). No third-party HTTP client means a smaller dependency-supply-chain
  surface and fewer transitive CVEs to track.
- **Timeouts everywhere.** Probes and transports are timeout-bounded so a hostile
  or dead host can't hang the app.

---

## 6. Robustness as a security property

Defensive coding limits the impact of malformed input and runtime faults:

- **Validation at the edges.** Config is validated structurally (`config.py`)
  and by content (`models.py`); bad input raises typed, human-readable
  exceptions rather than crashing in a widget callback. A malicious or corrupt
  config produces a clear error dialog, not undefined behaviour.
- **Atomic config saves.** `save_config` validates first, writes to a `*.tmp`
  file, then `os.replace`s it into place — a crash mid-write can't leave a
  truncated/half-valid config on disk.
- **Best-effort state.** A corrupt `state.json` is ignored in favour of defaults
  and never stops the app; only known keys are accepted, so a tampered/older
  file can't crash construction.
- **Global crash capture.** Uncaught exceptions on the main thread *and* worker
  threads are routed to the diagnostic log with full tracebacks via installed
  excepthooks — a background crash is never silently lost (important for a
  windowed build with no console).
- **Forward-compatible parsing.** Unknown fields are preserved, not executed —
  there is no `eval`-style interpretation of config.

---

## 7. Read-only install posture

The app is built to run from a **read-only** location (e.g. `Program Files`):

- Per-user preferences (`state.json`) and logs are written only to a writable
  per-user directory, never next to the binary.
- This supports locked-down workstation images where the application directory
  is not user-writable.

---

## 8. Threat model summary

| Threat | Mitigation |
|--------|------------|
| Infrastructure layout leaking via the code repo | Logic/data separation; `config.json` git-ignored; dummy-only example. |
| Credentials leaking via the repo | `auth` lives only in `config.json`; example uses placeholders. |
| Operator actions being unaccountable | Structured append-only audit log of every command, web-open, config change, and status check. |
| Malformed/hostile config crashing the app | Two-stage validation, typed exceptions, atomic saves, best-effort state. |
| A probe/command unintentionally changing device state | Status checks are read-only (TCP connect, or a single GET); commands are explicit and config-defined; everything is timeout-bounded. |
| Self-signed LAN certs forcing insecure global settings | TLS verification is **on by default**; opt-out is **per-command** and audited. |
| Background crash going unnoticed in a windowed build | Excepthooks capture main- and worker-thread crashes to the diagnostic log. |

### Out of scope / known limitations

- Credentials and the config are stored as **plaintext on disk**; protect with
  OS permissions. There is no built-in secret encryption.
- The audit log is append-only by convention, **not** cryptographically
  tamper-evident; forward it to an external collector for stronger assurance.
- mission-deck is an **operator console on a trusted LAN**; it assumes the host
  workstation and the AV network segment are themselves access-controlled.

---

## 9. Reporting a security issue

If you find real environment data committed anywhere, or a security defect in the
code, treat it as a **security incident**: report it **privately** to the
maintainers rather than opening a public issue.
