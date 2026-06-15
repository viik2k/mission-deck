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

HTTP commands may also set ``method`` (e.g. ``"POST"``), a ``body`` (placeholders
substituted too), ``headers``, ``auth`` (``{"username": …, "password": …}``) and
``verify_tls: false`` — enough to drive VC codec APIs (Cisco xCommand, Poly REST)
that need an authenticated POST over a self-signed-cert HTTPS endpoint.

The action functions are **blocking** and are executed on a background thread by
the app, so they never freeze the UI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

from mission_deck.browser import BrowserConfig, open_urls
from mission_deck.models import Device
from mission_deck.network import ControlError, http_request, tcp_send


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
    # The originating config ``commands`` entry, if any — lets the GUI offer an
    # "edit this command" affordance. None for built-ins like "Open Web UI".
    source: dict | None = None


def _format(template: str, device: Device, value: str | None) -> str:
    return (
        template
        .replace("{host}", device.host)
        .replace("{port}", str(device.port))
        .replace("{value}", value or "")
    )


def _format_url(template: str, device: Device, value: str | None) -> str:
    """Like _format but URL-encodes the {value} substitution.

    Prevents a user-entered value from breaking the URL structure or injecting
    extra path segments / query parameters.  {host} and {port} come from the
    trusted config and are left as-is.
    """
    return (
        template
        .replace("{host}", device.host)
        .replace("{port}", str(device.port))
        .replace("{value}", quote(value or "", safe=""))
    )


def _parse_auth(raw: object) -> tuple[str, str] | None:
    """Turn a config ``auth`` value into a ``(username, password)`` pair.

    Accepts ``{"username": …, "password": …}`` (``"user"`` is also honoured) or a
    two-item ``[username, password]`` list. Anything else yields ``None``.
    """

    if isinstance(raw, dict):
        user = raw.get("username", raw.get("user"))
        password = raw.get("password", raw.get("pass"))
        if user is not None and password is not None:
            return (str(user), str(password))
    elif isinstance(raw, (list, tuple)) and len(raw) == 2:
        return (str(raw[0]), str(raw[1]))
    return None


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
        method = str(spec.get("method", "GET"))
        body_tmpl = spec.get("body") if isinstance(spec.get("body"), str) else None
        auth = _parse_auth(spec.get("auth"))
        headers = spec.get("headers") if isinstance(spec.get("headers"), dict) else None
        verify_tls = bool(spec.get("verify_tls", True))

        def run(value: str | None, _tmpl=url_tmpl, _body=body_tmpl) -> str:
            body = _format(_body, device, value) if _body is not None else None
            return http_request(
                _format_url(_tmpl, device, value),
                method=method,
                body=body,
                timeout=float(spec.get("timeout", 5.0)),
                auth=auth,
                headers=headers,
                verify_tls=verify_tls,
            )

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

    return DeviceControl(
        id=cid, label=label, run=run, prompt=prompt, kind="command", source=spec
    )


def validate_command_spec(spec: dict) -> None:
    """Validate one ``commands`` entry, raising :class:`ControlError` if bad.

    Used by the GUI command editor so a user adding a control button gets a
    human-readable reason instead of a button that silently does nothing.
    """

    if not isinstance(spec, dict):
        raise ControlError("A command must be an object.")
    label = str(spec.get("label") or spec.get("id") or "").strip()
    if not label:
        raise ControlError("Give the command a label (the button text).")

    protocol = str(spec.get("protocol", "tcp")).lower()
    if protocol in ("http", "https"):
        if not str(spec.get("url") or "").strip():
            raise ControlError("An HTTP command needs a URL to request.")
        raw_auth = spec.get("auth")
        if "auth" in spec and raw_auth is not None and _parse_auth(raw_auth) is None:
            raise ControlError(
                "auth must be {\"username\": …, \"password\": …} or [username, password]."
            )
        raw_headers = spec.get("headers")
        if "headers" in spec and raw_headers is not None and not isinstance(raw_headers, dict):
            raise ControlError("headers must be an object of name/value pairs.")
    elif protocol == "tcp":
        payload = spec.get("payload")
        if not isinstance(payload, str) or not payload:
            raise ControlError("A TCP command needs a payload to send.")
        port = spec.get("port")
        if port is not None and (
            not isinstance(port, int) or isinstance(port, bool) or not (0 <= port <= 65535)
        ):
            raise ControlError("The port must be a whole number from 0 to 65535.")
    else:
        raise ControlError(
            f"Unsupported protocol {protocol!r}. Use 'http', 'https' or 'tcp'."
        )


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
