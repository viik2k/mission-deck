"""Typed data models for mission-deck rooms and devices.

This module turns the raw config dict produced by :mod:`mission_deck.config`
into a tree of typed objects the rest of the app can rely on:

    Site (all rooms)
      └── Room
            └── Device  (and type-specific subclasses)

Design goals
------------
* **Validation at the edge.** ``Device.from_dict`` / ``Room.from_dict`` raise a
  precise :class:`DeviceConfigError` for bad input so the GUI can surface a
  human-readable reason instead of crashing deep in a widget callback.

* **Control extensibility via subclassing.** Every device ``type`` maps to a
  concrete :class:`Device` subclass through a registry. The base class defines
  the control surface (``send_command``) as a not-yet-implemented hook; PTZ
  cameras, Crestron processors and DSPs override it in later steps. Adding a
  new device type is a single ``@register_device`` decorator away.

* **No I/O here.** Network reachability/status checking lives in
  ``network.py`` (Step 4). Models only *hold* a status value
  (:class:`DeviceStatus`) that the checker updates.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .config import SUPPORTED_SCHEMA_VERSION, LoadedConfig


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #
class DeviceConfigError(Exception):
    """A device or room entry in the config is missing/has invalid fields."""


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #
class DeviceStatus(enum.Enum):
    """Reachability state of a device, as shown by the UI indicator."""

    UNKNOWN = "unknown"      # never checked, or check not yet run
    CHECKING = "checking"    # a status check is currently in flight
    ONLINE = "online"        # reachable (green)
    OFFLINE = "offline"      # unreachable / timed out (red)

    @property
    def is_resolved(self) -> bool:
        """True once we have a definitive answer (online or offline)."""

        return self in (DeviceStatus.ONLINE, DeviceStatus.OFFLINE)


class RecordingStatus(enum.Enum):
    """Recording state of a Recorder device, polled separately from reachability."""

    UNKNOWN = "unknown"       # never checked, or device is offline
    IDLE = "idle"             # reachable but not recording
    RECORDING = "recording"   # actively recording
    PAUSED = "paused"         # recording session paused


# --------------------------------------------------------------------------- #
# Device base class
# --------------------------------------------------------------------------- #
# Required keys every device entry must provide.
_REQUIRED_DEVICE_KEYS = ("id", "name", "type", "host")

# Default network port per known protocol, used when a device omits "port".
_DEFAULT_PORTS: dict[str, int] = {
    "http": 80,
    "https": 443,
    "ssh": 22,
    "telnet": 23,
    "tcp": 0,   # protocol-agnostic raw TCP: a port must be supplied explicitly
}

# Ports that are implied by a web scheme and therefore omitted from a URL.
_WEB_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}

# Label used to group rooms that have no ``city`` set in the config.
DEFAULT_CITY = "Ungrouped"


@dataclass(slots=True)
class Device:
    """Base class for a single piece of AV equipment.

    Subclasses (registered via :func:`register_device`) specialise behaviour
    and, later, control commands. Instances are created through
    :meth:`from_dict` rather than constructed directly, so that the correct
    subclass is chosen based on the ``type`` field.
    """

    # --- Identity / config (populated from JSON) ---------------------------- #
    id: str
    name: str
    type: str
    host: str
    port: int = 0
    protocol: str = "tcp"
    manufacturer: str = ""
    model: str = ""
    tags: tuple[str, ...] = ()
    # Anything in the JSON we don't model explicitly is preserved here so
    # forward-compatible fields survive a load/edit round-trip.
    extra: dict[str, Any] = field(default_factory=dict)

    # --- Runtime state (not from JSON) -------------------------------------- #
    status: DeviceStatus = field(default=DeviceStatus.UNKNOWN, compare=False)
    last_latency_ms: float | None = field(default=None, compare=False)
    last_error: str | None = field(default=None, compare=False)

    # Human-friendly label for this device category; subclasses override.
    category: str = "Device"

    # ------------------------------------------------------------------ #
    # Construction / validation
    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Device":
        """Build the appropriate Device subclass from one JSON device entry.

        Dispatches on the ``type`` field to a registered subclass, falling back
        to :class:`GenericDevice` for unknown types so an unrecognised device
        never breaks loading an otherwise-valid room.
        """

        if not isinstance(data, dict):
            raise DeviceConfigError(
                f"device entry must be an object, got {type(data).__name__}"
            )

        for key in _REQUIRED_DEVICE_KEYS:
            value = data.get(key)
            if not isinstance(value, str) or not value.strip():
                raise DeviceConfigError(
                    f"device {data.get('id', '<no id>')!r}: "
                    f"missing or empty required string field {key!r}"
                )

        _raw_host = data["host"].strip()
        if "://" in _raw_host or _raw_host.startswith("//") or "@" in _raw_host:
            raise DeviceConfigError(
                f"device {data['id']!r}: 'host' must be a plain hostname or IP address "
                f"(remove any http:// prefix or @ character)."
            )

        device_type = data["type"].strip().lower()
        target_cls = _DEVICE_REGISTRY.get(device_type, GenericDevice)

        protocol = str(data.get("protocol", "tcp")).strip().lower() or "tcp"
        port = data.get("port")
        if port is None:
            port = _DEFAULT_PORTS.get(protocol, 0)
        if not isinstance(port, int) or isinstance(port, bool) or not (0 <= port <= 65535):
            raise DeviceConfigError(
                f"device {data['id']!r}: 'port' must be an integer 0-65535, "
                f"got {port!r}"
            )

        tags_raw = data.get("tags", [])
        if not isinstance(tags_raw, (list, tuple)):
            raise DeviceConfigError(
                f"device {data['id']!r}: 'tags' must be a list of strings"
            )
        tags = tuple(str(t) for t in tags_raw)

        # Preserve unknown keys for forward compatibility.
        known = {
            "id", "name", "type", "host", "port", "protocol",
            "manufacturer", "model", "tags",
        }
        extra = {k: v for k, v in data.items() if k not in known}

        return target_cls(
            id=data["id"].strip(),
            name=data["name"].strip(),
            type=device_type,
            host=data["host"].strip(),
            port=port,
            protocol=protocol,
            manufacturer=str(data.get("manufacturer", "")).strip(),
            model=str(data.get("model", "")).strip(),
            tags=tags,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this device back to its JSON config form.

        The inverse of :meth:`from_dict`. Unknown keys preserved in
        :attr:`extra` (``web_*`` overrides, ``commands``, …) are written back
        verbatim, so a load → edit → save round-trip never silently drops
        config a future build might rely on.
        """

        data: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "host": self.host,
            "port": self.port,
            "protocol": self.protocol,
        }
        if self.manufacturer:
            data["manufacturer"] = self.manufacturer
        if self.model:
            data["model"] = self.model
        if self.tags:
            data["tags"] = list(self.tags)
        # Preserved/forward-compatible fields (web_url, commands, …) last.
        for key, value in self.extra.items():
            data[key] = value
        return data

    # ------------------------------------------------------------------ #
    # Convenience
    # ------------------------------------------------------------------ #
    @property
    def address(self) -> str:
        """``host:port`` for display and connection purposes."""

        return f"{self.host}:{self.port}" if self.port else self.host

    @property
    def description(self) -> str:
        """Short ``Manufacturer Model`` label, falling back gracefully."""

        parts = [p for p in (self.manufacturer, self.model) if p]
        return " ".join(parts) if parts else self.category

    def has_tag(self, tag: str) -> bool:
        return tag.lower() in (t.lower() for t in self.tags)

    # ------------------------------------------------------------------ #
    # Web UI access
    # ------------------------------------------------------------------ #
    @property
    def web_url(self) -> str | None:
        """The device's browser-openable management UI, or ``None``.

        Resolution order:
          1. An explicit ``web_url`` field in the config (used verbatim).
          2. A web scheme derived from the device. The *control* protocol/port
             (used for status checks) is intentionally separate from the web
             UI: a Crestron processor may be controlled on TCP/41794 but expose
             its admin page on HTTP/80. Configure that with ``web_protocol``
             and/or ``web_port`` (falling back to ``protocol``/``port``).
          3. An optional ``web_path`` is appended (e.g. ``/setup``).

        Returns ``None`` for devices with no web interface (e.g. a raw-TCP or
        SSH-only device), so they are simply skipped by "Open Web UIs".
        """

        override = self.extra.get("web_url")
        if isinstance(override, str) and override.strip():
            return override.strip()

        scheme = self.extra.get("web_protocol")
        if not (isinstance(scheme, str) and scheme.lower() in _WEB_DEFAULT_PORTS):
            scheme = self.protocol if self.protocol in _WEB_DEFAULT_PORTS else None
        if scheme is None:
            return None
        scheme = scheme.lower()

        port = self.extra.get("web_port", self.port)
        if not isinstance(port, int) or isinstance(port, bool):
            port = self.port

        path = self.extra.get("web_path", "")
        if not isinstance(path, str):
            path = ""
        if path and not path.startswith("/"):
            path = "/" + path

        if port and port != _WEB_DEFAULT_PORTS.get(scheme):
            return f"{scheme}://{self.host}:{port}{path}"
        return f"{scheme}://{self.host}{path}"

    @property
    def is_web_accessible(self) -> bool:
        return self.web_url is not None

    def reset_status(self) -> None:
        """Clear runtime check results back to UNKNOWN."""

        self.status = DeviceStatus.UNKNOWN
        self.last_latency_ms = None
        self.last_error = None

    # ------------------------------------------------------------------ #
    # Control surface (extension point for later steps)
    # ------------------------------------------------------------------ #
    async def send_command(self, command: str, **kwargs: Any) -> Any:
        """Send a control command to the device.

        The base implementation is a deliberate placeholder. Concrete device
        types (PTZ cameras, Crestron processors, DSPs) override this in a later
        step to speak their real protocol (HTTP/CGI, TCP, etc.). Keeping the
        signature on the base class gives the UI a single, uniform way to issue
        commands regardless of device type.
        """

        raise NotImplementedError(
            f"{type(self).__name__} ({self.type!r}) does not implement control "
            f"commands yet."
        )

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.name} [{self.category}] @ {self.address}"


# --------------------------------------------------------------------------- #
# Device type registry
# --------------------------------------------------------------------------- #
_DEVICE_REGISTRY: dict[str, type[Device]] = {}


def register_device(*type_names: str) -> Callable[[type[Device]], type[Device]]:
    """Class decorator registering a Device subclass for one or more types.

    Example
    -------
    >>> @register_device("ptz_camera")
    ... class PTZCamera(Device):
    ...     category = "PTZ Camera"
    """

    def decorator(klass: type[Device]) -> type[Device]:
        for name in type_names:
            key = name.strip().lower()
            if key in _DEVICE_REGISTRY:
                raise ValueError(f"device type {key!r} is already registered")
            _DEVICE_REGISTRY[key] = klass
        return klass

    return decorator


def registered_device_types() -> dict[str, type[Device]]:
    """Read-only view of the type → class mapping (handy for tests/UI)."""

    return dict(_DEVICE_REGISTRY)


# --------------------------------------------------------------------------- #
# Concrete device types
# --------------------------------------------------------------------------- #
# These subclasses currently only customise presentation (``category``). They
# are the seams where real control logic will be added later; each can override
# ``send_command`` to speak its native protocol.
@register_device("ptz_camera", "camera")
@dataclass(slots=True)
class PTZCamera(Device):
    category: str = "PTZ Camera"


@register_device("control_processor", "crestron", "controller")
@dataclass(slots=True)
class ControlProcessor(Device):
    category: str = "Control Processor"


@register_device("audio_dsp", "dsp")
@dataclass(slots=True)
class AudioDSP(Device):
    category: str = "Audio DSP"


@register_device("display", "monitor", "tv")
@dataclass(slots=True)
class Display(Device):
    category: str = "Display"


@register_device("document_camera", "doc_camera")
@dataclass(slots=True)
class DocumentCamera(Device):
    category: str = "Document Camera"


@register_device("recorder")
@dataclass(slots=True)
class Recorder(Device):
    category: str = "Recorder"
    recording_status: RecordingStatus = field(default=RecordingStatus.UNKNOWN, compare=False)

    @property
    def recording_status_url(self) -> str | None:
        url = self.extra.get("recording_status_url")
        return str(url).strip() if isinstance(url, str) and url.strip() else None

    @property
    def recording_start_url(self) -> str | None:
        url = self.extra.get("recording_start_url")
        return str(url).strip() if isinstance(url, str) and url.strip() else None

    @property
    def recording_stop_url(self) -> str | None:
        url = self.extra.get("recording_stop_url")
        return str(url).strip() if isinstance(url, str) and url.strip() else None

    @property
    def recording_status_json_path(self) -> str:
        """Dot-path into the status URL's JSON response (e.g. ``"state.recording"``).

        Empty string means "use the whole response body" — see
        :func:`network.fetch_recording_status`.
        """
        path = self.extra.get("recording_status_json_path")
        return str(path).strip() if isinstance(path, str) and path.strip() else ""


@register_device(
    "video_matrix", "matrix_switcher", "av_matrix", "blustream",
    "nvx", "extron_matrix", "amx_matrix", "atlona", "wyrestorm",
)
@dataclass(slots=True)
class VideoMatrix(Device):
    """Video matrix / AV-over-IP switcher controller (Blustream, Crestron NVX, …)."""

    category: str = "Video Matrix"


@register_device("video_encoder", "av_encoder", "encoder", "tx", "blustream_tx", "nvx_tx")
@dataclass(slots=True)
class VideoEncoder(Device):
    """AV-over-IP transmit endpoint (encoder/TX), typically on the in-room LAN."""

    category: str = "Video Encoder (TX)"


@register_device("video_decoder", "av_decoder", "decoder", "rx", "blustream_rx", "nvx_rx")
@dataclass(slots=True)
class VideoDecoder(Device):
    """AV-over-IP receive endpoint (decoder/RX), typically on the in-room LAN."""

    category: str = "Video Decoder (RX)"


@register_device(
    "vc_codec", "video_conference", "codec", "cisco_webex", "webex",
    "poly", "lifesize", "zoom_room",
)
@dataclass(slots=True)
class VideoConferenceCodec(Device):
    """Video-conferencing codec (Cisco Webex, Poly, Lifesize, Zoom Room, …)."""

    category: str = "Video Conferencing"


@dataclass(slots=True)
class GenericDevice(Device):
    """Fallback for device types not explicitly modelled."""

    category: str = "Generic Device"


# --------------------------------------------------------------------------- #
# Room
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Room:
    """A single courtroom (or space) and the devices installed in it."""

    id: str
    name: str
    city: str = ""
    location: str = ""
    notes: str = ""
    devices: list[Device] = field(default_factory=list)
    # Unknown room-level keys, preserved across a load/save round-trip.
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Room":
        if not isinstance(data, dict):
            raise DeviceConfigError(
                f"room entry must be an object, got {type(data).__name__}"
            )

        room_id = data.get("id")
        name = data.get("name")
        if not isinstance(room_id, str) or not room_id.strip():
            raise DeviceConfigError("room entry missing required string field 'id'")
        if not isinstance(name, str) or not name.strip():
            raise DeviceConfigError(
                f"room {room_id!r}: missing required string field 'name'"
            )

        raw_devices = data.get("devices", [])
        if not isinstance(raw_devices, list):
            raise DeviceConfigError(f"room {room_id!r}: 'devices' must be a list")

        devices: list[Device] = []
        seen_ids: set[str] = set()
        for entry in raw_devices:
            device = Device.from_dict(entry)
            if device.id in seen_ids:
                raise DeviceConfigError(
                    f"room {room_id!r}: duplicate device id {device.id!r}"
                )
            seen_ids.add(device.id)
            devices.append(device)

        known = {"id", "name", "city", "location", "notes", "devices"}
        extra = {k: v for k, v in data.items() if k not in known}

        return cls(
            id=room_id.strip(),
            name=name.strip(),
            city=str(data.get("city", "")).strip(),
            location=str(data.get("location", "")).strip(),
            notes=str(data.get("notes", "")).strip(),
            devices=devices,
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise this room (and its devices) back to JSON config form."""

        data: dict[str, Any] = {"id": self.id, "name": self.name}
        if self.city:
            data["city"] = self.city
        if self.location:
            data["location"] = self.location
        if self.notes:
            data["notes"] = self.notes
        for key, value in self.extra.items():
            data[key] = value
        data["devices"] = [d.to_dict() for d in self.devices]
        return data

    @property
    def device_count(self) -> int:
        return len(self.devices)

    def devices_by_category(self) -> dict[str, list[Device]]:
        """Group devices by their display category, preserving insertion order."""

        grouped: dict[str, list[Device]] = {}
        for device in self.devices:
            grouped.setdefault(device.category, []).append(device)
        return grouped

    def get_device(self, device_id: str) -> Device | None:
        return next((d for d in self.devices if d.id == device_id), None)

    def web_devices(self) -> list[Device]:
        """Devices in this room that expose a browser-openable web UI."""

        return [d for d in self.devices if d.is_web_accessible]

    def web_urls(self) -> list[str]:
        """De-duplicated web UI URLs for this room, in device order."""

        urls: list[str] = []
        seen: set[str] = set()
        for device in self.devices:
            url = device.web_url
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
        return urls

    @property
    def room_health(self) -> DeviceStatus:
        """Aggregate health based on all device statuses in this room.

        Returns:
            ONLINE  — every device is ONLINE.
            OFFLINE — every device is OFFLINE.
            CHECKING / amber — mixed (some online, some offline / checking).
            UNKNOWN / gray — no devices, or every device is UNKNOWN.
        """

        if not self.devices:
            return DeviceStatus.UNKNOWN
        statuses = {d.status for d in self.devices}
        if all(s == DeviceStatus.UNKNOWN for s in statuses):
            return DeviceStatus.UNKNOWN
        if all(s == DeviceStatus.ONLINE for s in statuses):
            return DeviceStatus.ONLINE
        if all(s == DeviceStatus.OFFLINE for s in statuses):
            return DeviceStatus.OFFLINE
        return DeviceStatus.CHECKING  # amber for mixed / in-progress

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.name} ({self.device_count} devices)"


# --------------------------------------------------------------------------- #
# Site (top-level collection)
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Site:
    """All rooms loaded from a config, plus app-level settings."""

    rooms: list[Room] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_loaded_config(cls, loaded: LoadedConfig) -> "Site":
        """Build a :class:`Site` from a :class:`~mission_deck.config.LoadedConfig`.

        The config layer has already validated the *structure* (rooms is a
        list, schema version matches). Here we validate and type the
        *contents*.
        """

        rooms: list[Room] = []
        seen_ids: set[str] = set()
        for entry in loaded.rooms:
            room = Room.from_dict(entry)
            if room.id in seen_ids:
                raise DeviceConfigError(f"duplicate room id {room.id!r}")
            seen_ids.add(room.id)
            rooms.append(room)

        return cls(rooms=rooms, settings=loaded.app_settings)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the whole site back to a config dict ready to write.

        Produces the ``{schema_version, app, rooms}`` shape understood by
        :func:`mission_deck.config.load_config`. The ``app`` settings block is
        preserved as loaded.
        """

        data: dict[str, Any] = {"schema_version": SUPPORTED_SCHEMA_VERSION}
        if self.settings:
            data["app"] = self.settings
        data["rooms"] = [r.to_dict() for r in self.rooms]
        return data

    @property
    def ping_timeout_seconds(self) -> float:
        try:
            return float(self.settings.get("ping_timeout_seconds", 2.0))
        except (TypeError, ValueError):
            return 2.0

    @property
    def max_concurrent_checks(self) -> int:
        """Config-level cap on simultaneous status probes (0 = use the default).

        Lets a large deployment tune how many connections a sweep opens at once
        without code changes; an app-state preference overrides it (see
        ``app._effective_concurrency``).
        """

        try:
            value = int(self.settings.get("max_concurrent_checks", 0))
        except (TypeError, ValueError):
            return 0
        return value if value > 0 else 0

    def get_room(self, room_id: str) -> Room | None:
        return next((r for r in self.rooms if r.id == room_id), None)

    def all_devices(self) -> Iterable[Device]:
        for room in self.rooms:
            yield from room.devices

    def grouped_by_city(self) -> dict[str, list[Room]]:
        """Group rooms by ``city``, preserving first-seen city order.

        Rooms with no city fall under :data:`DEFAULT_CITY`. Within each city,
        room order from the config is preserved.
        """

        groups: dict[str, list[Room]] = {}
        for room in self.rooms:
            groups.setdefault(room.city or DEFAULT_CITY, []).append(room)
        return groups

    @property
    def is_multi_city(self) -> bool:
        """True when grouping rooms by city is meaningful.

        False when every room is ungrouped (no ``city`` anywhere), so the UI
        can fall back to a flat room list instead of a single pointless header.
        """

        cities = self.grouped_by_city().keys()
        return list(cities) != [DEFAULT_CITY]
