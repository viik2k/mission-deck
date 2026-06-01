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
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Iterable

from .models import Device, DeviceStatus


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

def http_get(url: str, timeout: float = 5.0) -> str:
    """Issue an HTTP(S) GET and return a short human-readable result."""

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            status = getattr(response, "status", response.getcode())
            return f"HTTP {status} OK"
    except urllib.error.HTTPError as exc:
        # The server answered, just not 2xx — still a "reached it" outcome.
        return f"HTTP {exc.code} {exc.reason}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ControlError(f"Request failed: {reason}") from exc


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
