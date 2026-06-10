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
import logging
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from .models import Device, DeviceStatus, RecordingStatus

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CheckResult:
    """Outcome of probing a single device."""

    device_id: str
    status: DeviceStatus
    latency_ms: float | None = None
    error: str | None = None


# --------------------------------------------------------------------------- #
# Monitor registry ("monitoring plugins")
# --------------------------------------------------------------------------- #
# A *monitor* answers "is this device healthy right now?" and returns a
# :class:`CheckResult`. The default ``tcp`` monitor opens a TCP connection (the
# original behaviour); ``http`` issues a request and treats any answered
# endpoint as up; ``ping`` sends one ICMP echo via the system ping binary.
# New check types register with :func:`register_monitor` — the
# same extensibility seam as ``@register_device`` in ``models.py`` — and a
# device opts in with a ``"monitor"`` key in its config (falling back to ``tcp``).
MonitorFn = Callable[[Device, float], Awaitable[CheckResult]]

_MONITOR_REGISTRY: dict[str, MonitorFn] = {}

# The monitor used when a device names none. Kept tcp so every existing config
# behaves exactly as before this registry existed.
DEFAULT_MONITOR = "tcp"


def register_monitor(*names: str) -> Callable[[MonitorFn], MonitorFn]:
    """Register an async monitor function under one or more names.

    Example
    -------
    >>> @register_monitor("ping")
    ... async def ping_monitor(device, timeout):
    ...     ...
    """

    def decorator(fn: MonitorFn) -> MonitorFn:
        for name in names:
            key = name.strip().lower()
            if key in _MONITOR_REGISTRY:
                raise ValueError(f"monitor {key!r} is already registered")
            _MONITOR_REGISTRY[key] = fn
        return fn

    return decorator


def monitor_name_for(device: Device) -> str:
    """The monitor name a device wants, or :data:`DEFAULT_MONITOR`.

    An explicit ``"monitor"`` config key wins; otherwise we keep the historical
    behaviour of a TCP-connect probe regardless of protocol.
    """

    name = device.extra.get("monitor")
    if isinstance(name, str) and name.strip().lower() in _MONITOR_REGISTRY:
        return name.strip().lower()
    return DEFAULT_MONITOR


def registered_monitors() -> dict[str, MonitorFn]:
    """Read-only view of the registered monitor names (handy for tests/UI)."""

    return dict(_MONITOR_REGISTRY)


@register_monitor("tcp")
async def _tcp_monitor(device: Device, timeout: float) -> CheckResult:
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
        logger.debug("Probe %s (%s:%s) timed out after %.1fs", device.id, device.host, port, timeout)
        return CheckResult(device.id, DeviceStatus.OFFLINE, None, "timed out")
    except (OSError, asyncio.CancelledError) as exc:
        # Connection refused, host unreachable, DNS failure, etc.
        logger.debug("Probe %s (%s:%s) failed: %s", device.id, device.host, port, exc)
        return CheckResult(device.id, DeviceStatus.OFFLINE, None, str(exc) or "unreachable")

    latency_ms = (time.perf_counter() - start) * 1000.0
    # We only needed to know the port accepts connections; close cleanly.
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    logger.debug("Probe %s (%s:%s) online in %.0fms", device.id, device.host, port, latency_ms)
    return CheckResult(device.id, DeviceStatus.ONLINE, latency_ms, None)


@register_monitor("http", "https")
async def _http_monitor(device: Device, timeout: float) -> CheckResult:
    """Probe a device over HTTP(S): any *answered* endpoint counts as online.

    The URL is taken from an explicit ``health_url`` config key, falling back to
    the device's :pyattr:`~mission_deck.models.Device.web_url`. A reachable
    server (even one returning 4xx/5xx) means the box is up; only a transport
    failure (DNS/refused/timeout) is OFFLINE. Self-signed-cert codecs can set
    ``verify_tls: false`` to skip certificate verification.

    Falls back to the TCP monitor when no URL can be resolved, so a misconfigured
    device still gets a sensible reachability answer.
    """

    health = device.extra.get("health_url")
    url = health.strip() if isinstance(health, str) and health.strip() else device.web_url
    if not url:
        return await _tcp_monitor(device, timeout)

    verify_tls = bool(device.extra.get("verify_tls", True))
    start = time.perf_counter()
    try:
        # http_request is blocking (urllib); run it off the event loop so other
        # probes in the same gather() keep going concurrently.
        await asyncio.to_thread(http_request, url, timeout=timeout, verify_tls=verify_tls)
    except ControlError as exc:
        logger.debug("HTTP probe %s (%s) failed: %s", device.id, url, exc)
        return CheckResult(device.id, DeviceStatus.OFFLINE, None, str(exc) or "unreachable")

    latency_ms = (time.perf_counter() - start) * 1000.0
    logger.debug("HTTP probe %s (%s) online in %.0fms", device.id, url, latency_ms)
    return CheckResult(device.id, DeviceStatus.ONLINE, latency_ms, None)


# "time=3ms" (Windows), "time<1ms" (Windows sub-millisecond), "time=0.42 ms" (POSIX).
_PING_TIME_RE = re.compile(r"time[=<]\s*([\d.]+)\s*ms", re.IGNORECASE)


@register_monitor("ping", "icmp")
async def _ping_monitor(device: Device, timeout: float) -> CheckResult:
    """Probe a device with one ICMP echo via the system ``ping`` binary.

    For devices that answer ping but expose no TCP port worth connecting to
    (printers, badge readers, anything behind a port-filtering firewall). Opt
    in per device with ``"monitor": "ping"``. Raw ICMP sockets need elevation,
    so this shells out to the OS ping — one echo, timeout-bounded — and parses
    the reported round-trip time for latency.

    On Windows ``ping`` exits 0 even for "Destination host unreachable"
    replies relayed by a gateway, so a reply only counts when it carries a
    TTL (i.e. it actually came from the target).
    """

    timeout_s = max(0.5, timeout)
    if sys.platform == "win32":
        args = ["ping", "-n", "1", "-w", str(int(timeout_s * 1000)), device.host]
    else:
        args = ["ping", "-c", "1", "-W", str(max(1, round(timeout_s))), device.host]
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError as exc:  # ping binary missing/unrunnable
        logger.debug("Ping probe %s could not start: %s", device.id, exc)
        return CheckResult(device.id, DeviceStatus.UNKNOWN, None, f"ping unavailable: {exc}")

    try:
        # ping enforces its own timeout; the extra margin only guards a hung process.
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 2.0)
    except asyncio.TimeoutError:
        proc.kill()
        logger.debug("Ping probe %s (%s) timed out after %.1fs", device.id, device.host, timeout_s)
        return CheckResult(device.id, DeviceStatus.OFFLINE, None, "timed out")

    output = stdout.decode("utf-8", errors="replace") if stdout else ""
    reached = proc.returncode == 0
    if sys.platform == "win32":
        reached = reached and "ttl=" in output.lower()
    if not reached:
        logger.debug("Ping probe %s (%s) no reply (rc=%s)", device.id, device.host, proc.returncode)
        return CheckResult(device.id, DeviceStatus.OFFLINE, None, "no ping reply")

    match = _PING_TIME_RE.search(output)
    latency_ms = float(match.group(1)) if match else None
    logger.debug("Ping probe %s (%s) online (%s ms)", device.id, device.host, latency_ms)
    return CheckResult(device.id, DeviceStatus.ONLINE, latency_ms, None)


async def check_device(device: Device, timeout: float) -> CheckResult:
    """Probe one device using its configured monitor (TCP connect by default)."""

    monitor = _MONITOR_REGISTRY.get(monitor_name_for(device), _tcp_monitor)
    return await monitor(device, timeout)


# How many device probes may be in flight at once. The estate-wide sweep can
# span the whole site (thousands of devices); firing them all simultaneously
# would exhaust file descriptors, flood the network with a SYN burst and stall
# the host. A semaphore caps concurrency so a sweep of any size runs as a
# steady, bounded stream of checks. Tunable per deployment via the
# ``max_concurrent_checks`` app/config setting (see ``app._effective_concurrency``).
DEFAULT_MAX_CONCURRENCY = 128


async def _check_all(
    devices: list[Device],
    timeout: float,
    publish: Callable[[CheckResult], None],
    concurrency: int,
) -> None:
    semaphore = asyncio.Semaphore(concurrency)

    async def probe(device: Device) -> None:
        # Hold the slot only for the probe itself; publish (a cheap queue put)
        # happens after release so a slow callback can't throttle throughput.
        async with semaphore:
            result = await check_device(device, timeout)
        publish(result)

    # Probes run concurrently up to ``concurrency``; publish fires as each
    # finishes, so results still stream to the UI live rather than in a batch.
    await asyncio.gather(*(probe(d) for d in devices))


def run_status_checks(
    devices: Iterable[Device],
    timeout: float,
    publish: Callable[[CheckResult], None],
    concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> None:
    """Blocking driver — run this on a worker thread.

    Probes every device with **at most** ``concurrency`` checks in flight at
    once, invoking ``publish(result)`` as each completes. Returns once all
    probes are done. Bounding concurrency is what lets an estate-wide sweep of
    thousands of devices run without exhausting sockets or stalling the host.
    """

    device_list = list(devices)
    if not device_list:
        return
    concurrency = max(1, min(concurrency, len(device_list)))

    # A dedicated event loop for this worker thread (we are never on the main
    # thread here, so there is no running loop to clash with). The HTTP monitor
    # offloads its blocking urllib call via ``asyncio.to_thread``; size the
    # default executor to match ``concurrency`` so those probes are genuinely
    # parallel rather than queueing behind the stdlib default (~32 workers).
    loop = asyncio.new_event_loop()
    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="md-probe")
    )
    try:
        loop.run_until_complete(_check_all(device_list, timeout, publish, concurrency))
    finally:
        # Wind the executor down before closing the loop so no probe thread is
        # left running against a closed loop.
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()


# --------------------------------------------------------------------------- #
# One-shot device control transports (blocking; run on a worker thread)
# --------------------------------------------------------------------------- #
# These power the device control actions (see ``controls.py``). They are simple,
# synchronous, and timeout-bounded — a single command at a time — and meant to
# be executed off the UI thread by the app's background runner.

def _assert_http_url(url: str) -> None:
    """Raise ControlError if *url* doesn't use the http or https scheme.

    Prevents file://, dict://, ftp:// and other schemes from being handed to
    urllib and potentially reading local files or triggering unexpected handlers.
    Called at the start of every outbound HTTP function.
    """
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ControlError(
            f"URL scheme {scheme!r} is not permitted; only http and https are allowed."
        )


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

    _assert_http_url(url)
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
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    logger.debug("HTTP %s %s (verify_tls=%s)", request.get_method(), url, verify_tls)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            status = getattr(response, "status", response.getcode())
            logger.info("HTTP %s %s -> %s", request.get_method(), url, status)
            return f"HTTP {status} OK"
    except urllib.error.HTTPError as exc:
        # The server answered, just not 2xx — still a "reached it" outcome.
        logger.info("HTTP %s %s -> %s %s", request.get_method(), url, exc.code, exc.reason)
        return f"HTTP {exc.code} {exc.reason}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        logger.warning("HTTP %s %s failed: %s", request.get_method(), url, reason)
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
    logger.debug("TCP send %d bytes to %s:%s (read_response=%s)", len(payload), host, port, read_response)
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.sendall(payload)
            if not read_response:
                logger.info("TCP sent %d bytes to %s:%s", len(payload), host, port)
                return f"Sent {len(payload)} bytes to {host}:{port}"
            sock.settimeout(timeout)
            try:
                data = sock.recv(2048)
            except socket.timeout:
                logger.info("TCP sent to %s:%s; no reply before timeout", host, port)
                return "Sent; no response (timed out waiting for reply)"
            text = data.decode("utf-8", errors="replace").strip()
            logger.info("TCP %s:%s replied %d bytes", host, port, len(data))
            return f"Reply: {text}" if text else "Sent; empty reply"
    except OSError as exc:
        logger.warning("TCP connection to %s:%s failed: %s", host, port, exc)
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
    scheme = urllib.parse.urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        logger.debug("Recording status URL has disallowed scheme %r: %s", scheme, url)
        return RecordingStatus.UNKNOWN
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
    except (urllib.error.URLError, OSError, socket.timeout) as exc:
        logger.debug("Recording status fetch from %s failed: %s", url, exc)
        return RecordingStatus.UNKNOWN
    except (ValueError, TypeError, KeyError) as exc:
        logger.debug("Could not parse recording status from %s: %s", url, exc)
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
