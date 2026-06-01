"""Per-device control actions — the UI-facing control layer.

Each device can expose a list of :class:`DeviceControl` actions (buttons) that
the UI renders in a control dialog. Actions come from two places:

  1. **Built-ins** — e.g. "Open Web UI" for any web-accessible device.
  2. **Config-driven commands** — a device's ``commands`` array in the config,
     so new control buttons can be added with zero code. This keeps the app
     vendor-neutral: instead of hard-coding a Crestron/Sony API, you describe
     the actual command (an HTTP URL or a TCP payload) in the config.

Example config (inside a device object):

```jsonc
"commands": [
  {"id": "preset1", "label": "Camera Preset 1", "protocol": "http",
   "url": "http://{host}/cgi-bin/ptzctrl?action=recall&preset=1"},
  {"id": "power_on", "label": "Power On", "protocol": "tcp",
   "payload": "PWR ON\r", "port": 4998},
  {"id": "send", "label": "Send Command", "protocol": "tcp",
   "payload": "{value}\r", "prompt": "Command to send", "read_response": true}
]
```

Placeholders ``{host}``, ``{port}`` and ``{value}`` are substituted at run time.
A ``prompt`` makes the UI ask the user for ``{value}`` first.

The action functions are **blocking** and are executed on a background thread by
the app, so they never freeze the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from mission_deck.browser import BrowserConfig, open_urls
from mission_deck.models import Device
from mission_deck.network import ControlError, http_get, tcp_send


@dataclass(slots=True)
class DeviceControl:
    """One actionable control for a device (rendered as a button)."""

    id: str
    label: str
    # run(value) -> result message. ``value`` is the user-entered text when
    # ``prompt`` is set, otherwise None.
    run: Callable[[str | None], str]
    prompt: str | None = None  # when set, the UI asks for a value first
    kind: str = "command"      # "command" | "web" — lets the UI style them


def _format(template: str, device: Device, value: str | None) -> str:
    return (
        template
        .replace("{host}", device.host)
        .replace("{port}", str(device.port))
        .replace("{value}", value or "")
    )


def _make_command_control(device: Device, spec: dict) -> DeviceControl | None:
    """Build a DeviceControl from one config ``commands`` entry."""

    if not isinstance(spec, dict):
        return None
    cid = str(spec.get("id") or spec.get("label") or "command")
    label = str(spec.get("label") or cid)
    protocol = str(spec.get("protocol", "tcp")).lower()
    prompt = spec.get("prompt")
    prompt = str(prompt) if prompt else None

    if protocol in ("http", "https"):
        url_tmpl = spec.get("url")
        if not isinstance(url_tmpl, str) or not url_tmpl:
            return None

        def run(value: str | None, _tmpl=url_tmpl) -> str:
            return http_get(_format(_tmpl, device, value), timeout=float(spec.get("timeout", 5.0)))

    elif protocol == "tcp":
        payload_tmpl = spec.get("payload")
        if not isinstance(payload_tmpl, str):
            return None
        port = spec.get("port")
        port = int(port) if isinstance(port, int) and not isinstance(port, bool) else device.port
        read_response = bool(spec.get("read_response", False))

        def run(value: str | None, _tmpl=payload_tmpl, _port=port, _rr=read_response) -> str:
            payload = _format(_tmpl, device, value).encode("utf-8", errors="replace")
            return tcp_send(device.host, _port, payload, timeout=float(spec.get("timeout", 5.0)),
                            read_response=_rr)

    else:
        return None

    return DeviceControl(id=cid, label=label, run=run, prompt=prompt, kind="command")


def controls_for(device: Device, browser_cfg: BrowserConfig) -> list[DeviceControl]:
    """All control actions available for ``device``, in display order."""

    controls: list[DeviceControl] = []

    # Built-in: open the device's web UI (if it has one).
    web_url = device.web_url
    if web_url:
        def open_web(_value: str | None, _url=web_url) -> str:
            open_urls([_url], browser_cfg)
            return f"Opening {_url}"

        controls.append(
            DeviceControl(id="__web", label="Open Web UI", run=open_web, kind="web")
        )

    # Config-driven commands.
    for spec in device.extra.get("commands", []) or []:
        control = _make_command_control(device, spec)
        if control is not None:
            controls.append(control)

    return controls
