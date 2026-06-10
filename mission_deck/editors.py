"""Config-editing dialogs — add/remove/edit rooms, devices and commands.

This is the layer that lets a non-technical user manage the equipment estate
*without ever opening config.json in a text editor*. Every dialog here:

  * collects fields with friendly labels and inline help,
  * validates through the same model/validation code paths the loader uses
    (so a value the UI accepts is a value the app can load), and
  * on save, mutates the live :class:`~mission_deck.models.Site` and asks the
    :class:`~mission_deck.app.App` to persist + refresh.

The dialogs never write files themselves — persistence and UI refresh are the
app's job, keeping all disk/UI orchestration in one place (``app.py``).
"""

from __future__ import annotations

import copy
import logging
import re
from typing import TYPE_CHECKING, Callable, Iterable

import customtkinter as ctk
from tkinter import messagebox

from mission_deck import __app_name__
from mission_deck.controls import validate_command_spec
from mission_deck.models import (
    Device,
    DeviceConfigError,
    Room,
    registered_device_types,
)
from mission_deck.network import ControlError
from mission_deck.theme import COLORS, CORNER, GAP, PAD
from mission_deck.ui import font

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # avoid a circular import at runtime (app imports editors)
    from mission_deck.app import App


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _slugify(text: str) -> str:
    """Turn a display name into a safe, lowercase id fragment."""

    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug or "item"


def unique_id(base: str, taken: Iterable[str]) -> str:
    """Return ``base`` (or ``base-2``, ``base-3``…) not already in ``taken``."""

    existing = set(taken)
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


# --------------------------------------------------------------------------- #
# Base form dialog
# --------------------------------------------------------------------------- #
class _FormDialog(ctk.CTkToplevel):
    """A scrollable, stacked-field form with a Cancel/Save button bar.

    Subclasses build their fields in ``__init__`` using the ``_entry`` /
    ``_combo`` / ``_switch`` / ``_textbox`` helpers, then call ``_build_buttons``.
    """

    def __init__(self, app: "App", title: str, geometry: str = "480x560"):
        super().__init__(app)
        self.app = app
        self.title(title)
        self.configure(fg_color=COLORS["bg"])
        self.geometry(geometry)
        self.transient(app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._body.grid(row=0, column=0, sticky="nsew", padx=PAD, pady=(PAD, 0))
        self._body.grid_columnconfigure(0, weight=1)
        self._row = 0

        self.after(60, lambda: (self.lift(), self.grab_set(), self.focus_force()))

    # -- field builders ------------------------------------------------- #
    def _field_frame(self, label: str, hint: str = "") -> ctk.CTkFrame:
        frame = ctk.CTkFrame(self._body, fg_color="transparent")
        frame.grid(row=self._row, column=0, sticky="ew", pady=(0, GAP))
        frame.grid_columnconfigure(0, weight=1)
        self._row += 1
        ctk.CTkLabel(
            frame, text=label, anchor="w",
            font=font(13, weight="bold"), text_color=COLORS["text"],
        ).grid(row=0, column=0, sticky="w")
        if hint:
            ctk.CTkLabel(
                frame, text=hint, anchor="w", justify="left",
                font=font(11), text_color=COLORS["text_faint"],
            ).grid(row=1, column=0, sticky="w", pady=(0, 2))
        # Lets a caller that needs to show/hide a whole field grab its frame.
        self._last_field = frame
        return frame

    def _entry(self, label: str, value: str = "", hint: str = "",
               placeholder: str = "") -> ctk.StringVar:
        frame = self._field_frame(label, hint)
        var = ctk.StringVar(value=value)
        ctk.CTkEntry(
            frame, textvariable=var, placeholder_text=placeholder,
            fg_color=COLORS["card"], border_color=COLORS["border"], border_width=1,
        ).grid(row=2, column=0, sticky="ew")
        return var

    def _combo(self, label: str, values: list[str], value: str = "",
               hint: str = "") -> ctk.StringVar:
        frame = self._field_frame(label, hint)
        var = ctk.StringVar(value=value)
        ctk.CTkComboBox(
            frame, values=values, variable=var,
            fg_color=COLORS["card"], border_color=COLORS["border"], border_width=1,
            button_color=COLORS["card_hover"], button_hover_color=COLORS["border"],
        ).grid(row=2, column=0, sticky="ew")
        return var

    def _switch(self, label: str, value: bool, hint: str = "") -> ctk.BooleanVar:
        frame = self._field_frame(label, hint)
        var = ctk.BooleanVar(value=value)
        ctk.CTkSwitch(frame, text="", variable=var).grid(row=2, column=0, sticky="w")
        return var

    def _textbox(self, label: str, value: str = "", hint: str = "",
                 height: int = 70) -> ctk.CTkTextbox:
        frame = self._field_frame(label, hint)
        box = ctk.CTkTextbox(
            frame, height=height, fg_color=COLORS["card"],
            border_color=COLORS["border"], border_width=1, wrap="word",
        )
        box.grid(row=2, column=0, sticky="ew")
        if value:
            box.insert("1.0", value)
        return box

    @staticmethod
    def _text_of(box: ctk.CTkTextbox) -> str:
        return box.get("1.0", "end").strip()

    # -- button bar ----------------------------------------------------- #
    def _build_buttons(
        self,
        on_save: Callable[[], None],
        save_text: str = "Save",
        on_delete: Callable[[], None] | None = None,
        delete_text: str = "Delete",
        on_duplicate: Callable[[], None] | None = None,
    ) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=PAD, pady=PAD)
        bar.grid_columnconfigure(0, weight=1)

        # Destructive / secondary actions sit on the left.
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        if on_delete is not None:
            ctk.CTkButton(
                left, text=delete_text, width=90, command=on_delete,
                fg_color="transparent", hover_color=COLORS["card_hover"],
                border_width=1, border_color=COLORS["offline"],
                text_color=COLORS["offline"],
            ).pack(side="left", padx=(0, GAP))
        if on_duplicate is not None:
            ctk.CTkButton(
                left, text="Duplicate", width=90, command=on_duplicate,
                fg_color="transparent", hover_color=COLORS["card_hover"],
                border_width=1, border_color=COLORS["border"], text_color=COLORS["text"],
            ).pack(side="left")

        ctk.CTkButton(
            bar, text="Cancel", width=90, command=self.destroy,
            fg_color="transparent", hover_color=COLORS["card_hover"],
            border_width=1, border_color=COLORS["border"], text_color=COLORS["text"],
        ).grid(row=0, column=1, padx=(0, GAP))
        ctk.CTkButton(
            bar, text=save_text, width=110, command=on_save,
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            font=font(13, weight="bold"),
        ).grid(row=0, column=2)

    def _error(self, message: str) -> None:
        logger.warning("%s: validation error shown to user: %s", type(self).__name__, message)
        messagebox.showerror(__app_name__, message, parent=self)

    def _confirm(self, message: str) -> bool:
        return messagebox.askyesno(__app_name__, message, parent=self)


# --------------------------------------------------------------------------- #
# Room editor
# --------------------------------------------------------------------------- #
class RoomEditorDialog(_FormDialog):
    """Add a new room, or edit / duplicate / delete an existing one."""

    def __init__(self, app: "App", room: Room | None = None):
        self.room = room
        editing = room is not None
        super().__init__(
            app,
            title="Edit Room" if editing else "Add Room",
            geometry="480x560",
        )

        self._name_var = self._entry(
            "Room name", room.name if editing else "",
            hint='Shown in the sidebar, e.g. "Courtroom 1A".',
            placeholder="Courtroom 1A",
        )
        self._id = self._entry(
            "ID (optional)", room.id if editing else "",
            hint="A short unique key. Leave blank to generate one from the name.",
            placeholder="courtroom-1a",
        )
        self._city = self._entry(
            "City / group (optional)", room.city if editing else "",
            hint="Rooms sharing a city are grouped together in the sidebar.",
            placeholder="Melbourne",
        )
        self._location = self._entry(
            "Location (optional)", room.location if editing else "",
            placeholder="1st Floor — East Wing",
        )
        self._notes = self._textbox(
            "Notes (optional)", room.notes if editing else "",
        )

        self._build_buttons(
            on_save=self._save,
            save_text="Save Room" if editing else "Add Room",
            on_delete=self._delete if editing else None,
            delete_text="Delete Room",
            on_duplicate=self._duplicate if editing else None,
        )

    def _other_room_ids(self) -> set[str]:
        return {r.id for r in self.app.site.rooms if r is not self.room}

    def _save(self) -> None:
        name = self._name_var.get().strip()
        if not name:
            self._error("Please enter a room name.")
            return
        rid = self._id.get().strip() or _slugify(name)
        if rid in self._other_room_ids():
            if self._id.get().strip():
                self._error(f"Another room already uses the ID {rid!r}.")
                return
            rid = unique_id(rid, self._other_room_ids())

        city = self._city.get().strip()
        location = self._location.get().strip()
        notes = self._text_of(self._notes)

        if self.room is None:
            room = Room(id=rid, name=name, city=city, location=location, notes=notes)
            self.app.site.rooms.append(room)
        else:
            room = self.room
            room.id, room.name = rid, name
            room.city, room.location, room.notes = city, location, notes

        if self.app.persist_config():
            self.app.refresh_sidebar()
            self.app.select_room(room)
            self.destroy()

    def _duplicate(self) -> None:
        assert self.room is not None
        rid = unique_id(f"{self.room.id}-copy", self._other_room_ids() | {self.room.id})
        clone = Room(
            id=rid,
            name=f"{self.room.name} (copy)",
            city=self.room.city,
            location=self.room.location,
            notes=self.room.notes,
            devices=[
                Device.from_dict(d.to_dict()) for d in self.room.devices
            ],
            extra=copy.deepcopy(self.room.extra),
        )
        self.app.site.rooms.append(clone)
        if self.app.persist_config():
            self.app.refresh_sidebar()
            self.app.select_room(clone)
            self.destroy()

    def _delete(self) -> None:
        assert self.room is not None
        if not self._confirm(
            f"Delete '{self.room.name}' and its {self.room.device_count} "
            f"device(s)?\n\nThis updates your config file."
        ):
            return
        self.app.site.rooms.remove(self.room)
        if self.app.persist_config():
            self.app.refresh_sidebar()
            self.app.select_first_or_empty()
            self.destroy()


# --------------------------------------------------------------------------- #
# Device editor
# --------------------------------------------------------------------------- #
class DeviceEditorDialog(_FormDialog):
    """Add a new device to a room, or edit / duplicate / delete one."""

    # Optional web-UI override keys handled explicitly (everything else in the
    # device's ``extra`` — e.g. ``commands`` — is preserved untouched).
    _WEB_KEYS = ("web_url", "web_protocol", "web_port", "web_path")

    def __init__(self, app: "App", room: Room, device: Device | None = None):
        self.room = room
        self.device = device
        editing = device is not None
        super().__init__(
            app,
            title="Edit Device" if editing else "Add Device",
            geometry="500x720",
        )

        d = device
        type_values = sorted(registered_device_types().keys())
        self._name_var = self._entry(
            "Device name", d.name if d else "",
            hint='Shown on the device card, e.g. "Judge Bench PTZ Camera".',
            placeholder="Judge Bench PTZ Camera",
        )
        self._id = self._entry(
            "ID (optional)", d.id if d else "",
            hint="Unique within this room. Leave blank to generate from the name.",
        )
        self._type = self._combo(
            "Type", type_values, d.type if d else "display",
            hint="Pick a known type or type your own; unknown types still work.",
        )
        self._host = self._entry(
            "Host / IP address", d.host if d else "",
            hint="The address used to reach the device.",
            placeholder="10.10.1.21",
        )
        self._port = self._entry(
            "Port (optional)", str(d.port) if (d and d.port) else "",
            hint="Leave blank to use the default for the protocol.",
            placeholder="80",
        )
        self._protocol = self._combo(
            "Protocol", ["http", "https", "tcp", "ssh", "telnet"],
            d.protocol if d else "tcp",
        )
        self._manufacturer = self._entry(
            "Manufacturer (optional)", d.manufacturer if d else "",
            placeholder="Sony",
        )
        self._model = self._entry(
            "Model (optional)", d.model if d else "",
            placeholder="SRG-300H",
        )
        self._tags = self._entry(
            "Tags (optional)", ", ".join(d.tags) if d else "",
            hint="Comma-separated labels, e.g. camera, video.",
        )

        # Advanced web-UI overrides (used by "Open Web UIs"). Optional.
        extra = d.extra if d else {}
        self._web_url = self._entry(
            "Web UI URL (optional)", str(extra.get("web_url", "")),
            hint="Full URL to the device's admin page. Overrides the fields below.",
            placeholder="http://10.10.1.21/setup",
        )
        self._web_protocol = self._combo(
            "Web UI protocol (optional)", ["", "http", "https"],
            str(extra.get("web_protocol", "")),
            hint="Use when the web UI differs from the control protocol above.",
        )
        self._web_port = self._entry(
            "Web UI port (optional)",
            str(extra["web_port"]) if isinstance(extra.get("web_port"), int) else "",
        )
        self._web_path = self._entry(
            "Web UI path (optional)", str(extra.get("web_path", "")),
            placeholder="/admin",
        )

        self._build_buttons(
            on_save=self._save,
            save_text="Save Device" if editing else "Add Device",
            on_delete=self._delete if editing else None,
            delete_text="Delete Device",
            on_duplicate=self._duplicate if editing else None,
        )

    def _other_device_ids(self) -> set[str]:
        return {dev.id for dev in self.room.devices if dev is not self.device}

    def _collect(self) -> dict | None:
        """Build a validated device dict from the form, or None on error."""

        name = self._name_var.get().strip()
        if not name:
            self._error("Please enter a device name.")
            return None
        host = self._host.get().strip()
        if not host:
            self._error("Please enter the device's host or IP address.")
            return None

        did = self._id.get().strip() or _slugify(name)
        if did in self._other_device_ids():
            if self._id.get().strip():
                self._error(f"Another device in this room uses the ID {did!r}.")
                return None
            did = unique_id(did, self._other_device_ids())

        # Preserve any extra keys we don't surface (e.g. commands), then layer
        # the web-UI overrides from the form on top.
        extra = dict(self.device.extra) if self.device else {}
        for key in self._WEB_KEYS:
            extra.pop(key, None)

        data: dict = {
            "id": did,
            "name": name,
            "type": self._type.get().strip() or "device",
            "host": host,
            "protocol": self._protocol.get().strip() or "tcp",
        }
        port_text = self._port.get().strip()
        if port_text:
            try:
                data["port"] = int(port_text)
            except ValueError:
                self._error("Port must be a whole number.")
                return None
        manufacturer = self._manufacturer.get().strip()
        if manufacturer:
            data["manufacturer"] = manufacturer
        model = self._model.get().strip()
        if model:
            data["model"] = model
        tags = [t.strip() for t in self._tags.get().split(",") if t.strip()]
        if tags:
            data["tags"] = tags

        if self._web_url.get().strip():
            extra["web_url"] = self._web_url.get().strip()
        if self._web_protocol.get().strip():
            extra["web_protocol"] = self._web_protocol.get().strip()
        web_port = self._web_port.get().strip()
        if web_port:
            try:
                extra["web_port"] = int(web_port)
            except ValueError:
                self._error("Web UI port must be a whole number.")
                return None
        if self._web_path.get().strip():
            extra["web_path"] = self._web_path.get().strip()

        data.update(extra)
        return data

    def _save(self) -> None:
        data = self._collect()
        if data is None:
            return
        try:
            built = Device.from_dict(data)
        except DeviceConfigError as exc:
            self._error(str(exc))
            return

        if self.device is None:
            self.room.devices.append(built)
        else:
            built.status = self.device.status  # keep the live status indicator
            index = self.room.devices.index(self.device)
            self.room.devices[index] = built

        if self.app.persist_config():
            self.app.refresh_current_room()
            self.destroy()

    def _duplicate(self) -> None:
        assert self.device is not None
        data = self.device.to_dict()
        data["id"] = unique_id(
            f"{self.device.id}-copy", self._other_device_ids() | {self.device.id}
        )
        data["name"] = f"{self.device.name} (copy)"
        try:
            clone = Device.from_dict(data)
        except DeviceConfigError as exc:
            self._error(str(exc))
            return
        self.room.devices.append(clone)
        if self.app.persist_config():
            self.app.refresh_current_room()
            self.destroy()

    def _delete(self) -> None:
        assert self.device is not None
        if not self._confirm(f"Remove '{self.device.name}' from this room?"):
            return
        self.room.devices.remove(self.device)
        if self.app.persist_config():
            self.app.refresh_current_room()
            self.destroy()


# --------------------------------------------------------------------------- #
# Control-command editor
# --------------------------------------------------------------------------- #
class CommandEditorDialog(_FormDialog):
    """Add / edit / remove one config-driven control command for a device.

    On save it mutates ``device.extra['commands']`` and fires ``on_saved`` so
    the control dialog can rebuild its buttons live.
    """

    def __init__(
        self,
        app: "App",
        device: Device,
        command: dict | None = None,
        on_saved: Callable[[], None] | None = None,
    ):
        self.device = device
        self.command = command
        self._on_saved = on_saved
        editing = command is not None
        super().__init__(
            app,
            title="Edit Command" if editing else "Add Command",
            geometry="500x640",
        )
        c = command or {}

        self._label = self._entry(
            "Button label", str(c.get("label", "")),
            hint='The text shown on the control button, e.g. "Power On".',
            placeholder="Power On",
        )
        self._id = self._entry(
            "ID (optional)", str(c.get("id", "")),
            hint="Leave blank to generate one from the label.",
        )
        self._protocol = self._combo(
            "How is the command sent?",
            ["tcp", "http", "https"],
            str(c.get("protocol", "tcp")).lower() or "tcp",
            hint="HTTP opens a web URL; TCP sends raw text to a port.",
        )

        # HTTP-only field.
        self._url = self._entry(
            "URL", str(c.get("url", "")),
            hint="Use {host} and {port} as placeholders for this device.",
            placeholder="http://{host}/cgi-bin/ptzctrl?action=recall&preset=1",
        )
        self._url_frame = self._last_field

        # TCP-only fields.
        self._payload = self._entry(
            "Text to send",
            str(c.get("payload", "")).replace("\r", "\\r").replace("\n", "\\n"),
            hint="Placeholders {host}, {port}, {value}. Use \\r / \\n for line endings.",
            placeholder="PWR ON\\r",
        )
        self._payload_frame = self._last_field
        self._port = self._entry(
            "Port (optional)",
            str(c["port"]) if isinstance(c.get("port"), int) else "",
            hint="Leave blank to reuse the device's port.",
        )
        self._port_frame = self._last_field
        self._read_response = self._switch(
            "Wait for a reply", bool(c.get("read_response", False)),
            hint="Read the device's response and show it after sending.",
        )
        self._read_frame = self._last_field

        # Shared optional fields.
        prompt = c.get("prompt")
        self._prompt = self._entry(
            "Ask the user first (optional)", str(prompt) if prompt else "",
            hint="If set, the app asks for a value (used as {value}) before sending.",
            placeholder="Command to send",
        )

        self._protocol.trace_add("write", lambda *_: self._sync_fields())
        self._sync_fields()

        self._build_buttons(
            on_save=self._save,
            save_text="Save Command" if editing else "Add Command",
            on_delete=self._delete if editing else None,
            delete_text="Delete",
        )

    def _sync_fields(self) -> None:
        """Show only the fields relevant to the chosen protocol."""

        is_http = self._protocol.get().strip().lower() in ("http", "https")
        self._url_frame.grid() if is_http else self._url_frame.grid_remove()
        for frame in (self._payload_frame, self._port_frame, self._read_frame):
            frame.grid_remove() if is_http else frame.grid()

    def _commands(self) -> list:
        return self.device.extra.setdefault("commands", [])

    def _save(self) -> None:
        label = self._label.get().strip()
        protocol = self._protocol.get().strip().lower() or "tcp"
        spec: dict = {
            "id": self._id.get().strip() or _slugify(label),
            "label": label,
            "protocol": protocol,
        }
        if protocol in ("http", "https"):
            spec["url"] = self._url.get().strip()
        else:
            # Let users type \r / \n and store the real control characters.
            spec["payload"] = (
                self._payload.get().replace("\\r", "\r").replace("\\n", "\n")
            )
            port_text = self._port.get().strip()
            if port_text:
                try:
                    spec["port"] = int(port_text)
                except ValueError:
                    self._error("Port must be a whole number.")
                    return
            if self._read_response.get():
                spec["read_response"] = True
        if self._prompt.get().strip():
            spec["prompt"] = self._prompt.get().strip()

        try:
            validate_command_spec(spec)
        except ControlError as exc:
            self._error(str(exc))
            return

        commands = self._commands()
        if self.command is None:
            commands.append(spec)
        else:
            try:
                commands[commands.index(self.command)] = spec
            except ValueError:
                commands.append(spec)

        self._finish()

    def _delete(self) -> None:
        if self.command is None:
            return
        if not self._confirm(f"Delete the \"{self.command.get('label', '')}\" command?"):
            return
        commands = self._commands()
        if self.command in commands:
            commands.remove(self.command)
        if not commands:
            self.device.extra.pop("commands", None)
        self._finish()

    def _finish(self) -> None:
        if self.app.persist_config():
            if self._on_saved is not None:
                self._on_saved()
            self.destroy()
