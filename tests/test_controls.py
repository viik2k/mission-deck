"""Tests for command template substitution in controls.py."""

from __future__ import annotations

import pytest

from mission_deck.controls import _format, _format_url
from mission_deck.models import Device


@pytest.fixture
def device() -> Device:
    return Device(id="cam1", name="Test Camera", type="ptz_camera", host="10.0.0.5", port=8080)


# --------------------------------------------------------------------------- #
# _format: plain substitution
# --------------------------------------------------------------------------- #
def test_format_host(device: Device) -> None:
    assert _format("http://{host}/cgi", device, None) == "http://10.0.0.5/cgi"


def test_format_port(device: Device) -> None:
    assert _format("{host}:{port}", device, None) == "10.0.0.5:8080"


def test_format_value(device: Device) -> None:
    assert _format("preset={value}", device, "3") == "preset=3"


def test_format_value_none_becomes_empty_string(device: Device) -> None:
    assert _format("preset={value}", device, None) == "preset="


def test_format_all_placeholders(device: Device) -> None:
    result = _format("tcp://{host}:{port}?v={value}", device, "hello world")
    assert result == "tcp://10.0.0.5:8080?v=hello world"


# --------------------------------------------------------------------------- #
# _format_url: value must be URL-encoded
# --------------------------------------------------------------------------- #
def test_format_url_encodes_space(device: Device) -> None:
    result = _format_url("http://{host}/search?q={value}", device, "hello world")
    assert result == "http://10.0.0.5/search?q=hello%20world"


def test_format_url_encodes_ampersand_and_equals(device: Device) -> None:
    result = _format_url("http://{host}/cmd?v={value}", device, "a&b=c")
    assert "a%26b%3Dc" in result


def test_format_url_encodes_slash(device: Device) -> None:
    result = _format_url("http://{host}/path?x={value}", device, "foo/bar")
    assert "foo%2Fbar" in result


def test_format_url_value_none_becomes_empty(device: Device) -> None:
    result = _format_url("http://{host}/path?q={value}", device, None)
    assert result.endswith("?q=")


def test_format_url_host_not_encoded(device: Device) -> None:
    result = _format_url("http://{host}/path", device, None)
    assert "10.0.0.5" in result


def test_format_url_port_substituted(device: Device) -> None:
    result = _format_url("http://{host}:{port}/path", device, None)
    assert "8080" in result


def test_format_url_host_value_differ(device: Device) -> None:
    # {host} should NOT be URL-encoded (it's trusted config), but {value} should be
    result = _format_url("http://{host}/cmd?v={value}", device, "x y")
    assert "10.0.0.5" in result
    assert "x%20y" in result
