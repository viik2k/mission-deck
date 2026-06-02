"""Asynchronous device reachability checking for mission-deck.

The status check answers a simple question per device — "can I open a TCP
connection to it right now?" — for every device in a room *concurrently*, with
a per-device timeout. A successful connect → ONLINE (with latency); a timeout
or refused/unreachable connection → OFFLINE.

Threading model
---------------
Tkinter is single-threaded, so this module never touches the UI. It exposes:

  * :func:`check_device` / async helpers for the actual probing, and
  * :func:`run_status_checks`, a *blocking* driver meant to be run on a worker
    thread. As each device resolves it invokes a ``publish`` callback with a
    :class:`CheckResult`.

The UI layer (``app.py``) runs :func:`run_status_checks` in a background thread
and marshals every ``publish`` back onto the Tk thread via ``widget.after(0,…)``
so cards flip green/red live, as results stream in, without freezing.
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from .models import Device, DeviceStatus, RecordingStatus


@dataclass(slots=True)
class CheckResult:
    """Outcome of probing a single device."""

    device_id: str
    status: DeviceStatus
    latency_ms: float | None = None
    error: str | None = None


async def check_device(device: Device, timeout: float) -> CheckResult:
    """Probe one device with a TCP connect, bounded by ``timeout`` seconds."""

    port = device.port
    if not port:
        # No port to test (e.g. a raw-TCP device with no port configured).
        return CheckResult(
            device.id, DeviceStatus.UNKNOWN, None, "no port configured to check"
        )

    start = time.perf_counter()
    try:
        connect = asyncio.open_connection(device.host, port)
        _reader, writer = await asyncio.wait_for(connect, timeout=timeout)
    except asyncio.TimeoutError:
        return CheckResult(device.id, DeviceStatus.OFFLINE, None, "timed out")
    except (OSError, asyncio.CancelledError) as exc:
        # Connection refused, host unreachable, DNS failure, etc.
        return CheckResult(device.id, DeviceStatus.OFFLINE, None, str(exc) or "unreachable")

    latency_ms = (time.perf_counter() - start) * 1000.0
    # We only needed to know the port accepts connections; close cleanly.
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return CheckResult(device.id, DeviceStatus.ONLINE, latency_ms, None)


async def _check_all(
    devices: list[Device],
    timeout: float,
    publish: Callable[[CheckResult], None],
) -> None:
    async def probe(device: Device) -> None:
        result = await check_device(device, timeout)
        publish(result)

    # All probes run concurrently; publish fires as each finishes.
    await asyncio.gather(*(probe(d) for d in devices))


def run_status_checks(
    devices: Iterable[Device],
    timeout: float,
    publish: Callable[[CheckResult], None],
) -> None:
    """Blocking driver — run this on a worker thread.

    Probes every device concurrently, invoking ``publish(result)`` as each
    completes. Returns once all probes are done.
    """

    device_list = list(devices)
    if not device_list:
        return

    # A dedicated event loop for this worker thread (we are never on the main
    # thread here, so there is no running loop to clash with).
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_check_all(device_list, timeout, publish))
    finally:
        loop.close()


# --------------------------------------------------------------------------- #
# One-shot device control transports (blocking; run on a worker thread)
# --------------------------------------------------------------------------- #
# These power the device control actions (see ``controls.py``). They are simple,
# synchronous, and timeout-bounded — a single command at a time — and meant to
# be executed off the UI thread by the app's background runner.

def http_request(
    url: str,
    method: str = "GET",
    body: str | None = None,
    timeout: float = 5.0,
    auth: Sequence[str] | None = None,
    headers: Mapping[str, Any] | None = None,
    verify_tls: bool = True,
) -> str:
    """Issue an HTTP(S) request and return a short human-readable result.

    Supports the GET-style probes used by simple devices *and* the POST-with-body
    APIs that VC codecs (Cisco xCommand, Poly) speak. All extras are optional:

      * ``method``     — "GET" (default), "POST", etc.
      * ``body``       — request body (str, sent UTF-8 encoded).
      * ``auth``       — ``(username, password)`` → an ``Authorization: Basic`` header.
      * ``headers``    — extra request headers.
      * ``verify_tls`` — when ``False``, skip certificate verification (courtroom
                         codecs commonly present self-signed certs on the LAN).
    """

    data = body.encode("utf-8", errors="replace") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method.upper())
    for key, value in (headers or {}).items():
        request.add_header(str(key), str(value))
    if auth is not None:
        user, password = auth[0], auth[1]
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        request.add_header("Authorization", f"Basic {token}")

    context: ssl.SSLContext | None = None
    if not verify_tls:
        context = ssl._create_unverified_context()

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status = getattr(response, "status", response.getcode())
            return f"HTTP {status} OK"
    except urllib.error.HTTPError as exc:
        # The server answered, just not 2xx — still a "reached it" outcome.
        return f"HTTP {exc.code} {exc.reason}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ControlError(f"Request failed: {reason}") from exc


def http_get(url: str, timeout: float = 5.0) -> str:
    """Issue an HTTP(S) GET and return a short human-readable result."""

    return http_request(url, method="GET", timeout=timeout)


def tcp_send(
    host: str,
    port: int,
    payload: bytes,
    timeout: float = 5.0,
    read_response: bool = False,
) -> str:
    """Open a TCP socket, send ``payload``, optionally read a short reply."""

    if not port:
        raise ControlError("No port configured for this command.")
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(payload)
            if not read_response:
                return f"Sent {len(payload)} bytes to {host}:{port}"
            sock.settimeout(timeout)
            try:
                data = sock.recv(2048)
            except socket.timeout:
                return "Sent; no response (timed out waiting for reply)"
            text = data.decode("utf-8", errors="replace").strip()
            return f"Reply: {text}" if text else "Sent; empty reply"
    except OSError as exc:
        raise ControlError(f"Connection failed: {exc}") from exc


class ControlError(Exception):
    """A device control command could not be completed."""


def fetch_recording_status(
    url: str, json_path: str = "", timeout: float = 5.0
) -> RecordingStatus:
    """GET ``url``, parse JSON, navigate ``json_path`` (dot-separated), return RecordingStatus.

    ``json_path`` example: ``"state.recording"`` navigates ``response["state"]["recording"]``.
    Returns UNKNOWN on any error (network, parse, unexpected value).
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data: Any = json.loads(raw)
        if json_path:
            for key in json_path.split("."):
                if isinstance(data, dict):
                    data = data.get(key)
                else:
                    return RecordingStatus.UNKNOWN
        return _parse_recording_value(data)
    except Exception:
        return RecordingStatus.UNKNOWN


def _parse_recording_value(value: Any) -> RecordingStatus:
    if isinstance(value, bool):
        return RecordingStatus.RECORDING if value else RecordingStatus.IDLE
    if isinstance(value, int):
        return RecordingStatus.RECORDING if value else RecordingStatus.IDLE
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("recording", "active", "true", "1", "on", "started", "running"):
            return RecordingStatus.RECORDING
        if v in ("paused", "pause", "suspended"):
            return RecordingStatus.PAUSED
        if v in ("idle", "stopped", "false", "0", "off", "inactive", "ready"):
            return RecordingStatus.IDLE
    return RecordingStatus.UNKNOWN
