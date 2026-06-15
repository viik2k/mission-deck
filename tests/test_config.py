"""Tests for config structural validation and example-config acceptance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mission_deck.config import (
    ConfigParseError,
    ConfigValidationError,
    example_config_path,
    load_config,
)
from mission_deck.models import DeviceConfigError, Room


def _write(tmp_path: Path, data: object) -> Path:
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
def test_accepts_example_config() -> None:
    loaded = load_config(example_config_path())
    assert loaded.schema_version == 1
    assert len(loaded.rooms) > 0


# --------------------------------------------------------------------------- #
# schema_version validation
# --------------------------------------------------------------------------- #
def test_rejects_missing_schema_version(tmp_path: Path) -> None:
    p = _write(tmp_path, {"rooms": []})
    with pytest.raises(ConfigValidationError, match="schema_version"):
        load_config(p)


def test_rejects_wrong_schema_version(tmp_path: Path) -> None:
    p = _write(tmp_path, {"schema_version": 99, "rooms": []})
    with pytest.raises(ConfigValidationError, match="unsupported"):
        load_config(p)


def test_rejects_non_integer_schema_version(tmp_path: Path) -> None:
    p = _write(tmp_path, {"schema_version": "1", "rooms": []})
    with pytest.raises(ConfigValidationError, match="schema_version"):
        load_config(p)


def test_rejects_top_level_non_object(tmp_path: Path) -> None:
    p = _write(tmp_path, [{"schema_version": 1}])
    with pytest.raises(ConfigValidationError, match="JSON object"):
        load_config(p)


# --------------------------------------------------------------------------- #
# rooms list validation
# --------------------------------------------------------------------------- #
def test_rejects_rooms_not_list(tmp_path: Path) -> None:
    p = _write(tmp_path, {"schema_version": 1, "rooms": "oops"})
    with pytest.raises(ConfigValidationError, match="rooms"):
        load_config(p)


def test_rejects_room_entry_not_dict(tmp_path: Path) -> None:
    p = _write(tmp_path, {"schema_version": 1, "rooms": ["not-a-dict"]})
    with pytest.raises(ConfigValidationError):
        load_config(p)


# --------------------------------------------------------------------------- #
# Parse error
# --------------------------------------------------------------------------- #
def test_rejects_invalid_json(tmp_path: Path) -> None:
    p = tmp_path / "config.json"
    p.write_text("{bad json", encoding="utf-8")
    with pytest.raises(ConfigParseError):
        load_config(p)


# --------------------------------------------------------------------------- #
# Device-field validation (Room.from_dict / DeviceConfigError)
# --------------------------------------------------------------------------- #
def test_rejects_missing_required_device_field() -> None:
    data = {
        "id": "r1",
        "name": "Room 1",
        "devices": [
            # "host" is required but absent
            {"id": "d1", "name": "Camera", "type": "ptz_camera"},
        ],
    }
    with pytest.raises(DeviceConfigError, match="host"):
        Room.from_dict(data)


def test_rejects_device_with_url_in_host() -> None:
    data = {
        "id": "r1",
        "name": "Room 1",
        "devices": [
            {"id": "d1", "name": "Camera", "type": "ptz_camera", "host": "http://10.0.0.1"},
        ],
    }
    with pytest.raises(DeviceConfigError, match="host"):
        Room.from_dict(data)


def test_rejects_duplicate_device_ids() -> None:
    data = {
        "id": "r1",
        "name": "Room 1",
        "devices": [
            {"id": "cam", "name": "Camera 1", "type": "ptz_camera", "host": "10.0.0.1"},
            {"id": "cam", "name": "Camera 2", "type": "ptz_camera", "host": "10.0.0.2"},
        ],
    }
    with pytest.raises(DeviceConfigError, match="duplicate"):
        Room.from_dict(data)
