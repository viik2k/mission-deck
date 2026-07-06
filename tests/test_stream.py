"""Tests for mission_deck.stream — the decode worker and its pure helpers.

The GUI half (:class:`LiveViewWindow`) needs a display and stays under manual
verification like the rest of the UI; everything below runs headless. The
worker tests substitute a small executable script for ffmpeg so live / stall /
reconnect behaviour is exercised for real, without cameras or codecs.
"""

from __future__ import annotations

import stat
import time
from collections.abc import Callable

import pytest

from mission_deck.models import Device
from mission_deck.stream import (
    FRAME_BYTES,
    StreamState,
    StreamWorker,
    build_ffmpeg_command,
    find_ffmpeg,
    redact_stream_url,
)


def wait_for(predicate: Callable[[], bool], timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def make_device(**extra) -> Device:
    return Device.from_dict(
        {"id": "cam1", "name": "Cam", "type": "ptz_camera", "host": "10.0.0.9", **extra}
    )


@pytest.fixture
def fake_ffmpeg(tmp_path):
    """An executable stand-in for ffmpeg: emits frames, then hangs or exits.

    Behaviour comes from env vars (FAKE_FRAMES, FAKE_THEN) because the worker
    controls the argv.
    """

    script = tmp_path / "fake-ffmpeg"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys, time\n"
        f"frame = b'*' * {FRAME_BYTES}\n"
        "out = sys.stdout.buffer\n"
        "for _ in range(int(os.environ.get('FAKE_FRAMES', '3'))):\n"
        "    out.write(frame)\n"
        "    out.flush()\n"
        "if os.environ.get('FAKE_THEN', 'hang') == 'hang':\n"
        "    time.sleep(60)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


# --------------------------------------------------------------------------- #
# Device.stream_url
# --------------------------------------------------------------------------- #
def test_stream_url_read_from_config() -> None:
    device = make_device(stream_url=" rtsp://10.0.0.9/media/video1 ")
    assert device.stream_url == "rtsp://10.0.0.9/media/video1"


def test_stream_url_missing_blank_or_non_string_is_none() -> None:
    assert make_device().stream_url is None
    assert make_device(stream_url="   ").stream_url is None
    assert make_device(stream_url=123).stream_url is None


def test_stream_url_survives_save_round_trip() -> None:
    device = make_device(stream_url="rtsp://10.0.0.9/media/video1")
    assert device.to_dict()["stream_url"] == "rtsp://10.0.0.9/media/video1"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_rtsp_command_forces_tcp_transport_before_input() -> None:
    cmd = build_ffmpeg_command("ffmpeg", "rtsp://cam/stream")
    transport = cmd.index("-rtsp_transport")
    assert cmd[transport + 1] == "tcp"
    assert transport < cmd.index("-i")
    assert cmd[cmd.index("-i") + 1] == "rtsp://cam/stream"
    assert cmd[-1] == "-"  # raw frames to stdout


def test_rtmp_command_has_no_rtsp_flags() -> None:
    cmd = build_ffmpeg_command("ffmpeg", "rtmp://server/live/cam")
    assert "-rtsp_transport" not in cmd
    assert "rawvideo" in cmd
    assert "rgb24" in cmd


def test_redact_strips_credentials() -> None:
    assert (
        redact_stream_url("rtsp://admin:hunter2@10.0.0.9:554/a")
        == "rtsp://10.0.0.9:554/a"
    )


def test_redact_leaves_plain_urls_alone() -> None:
    url = "rtmp://10.0.0.9/live/cam"
    assert redact_stream_url(url) == url


def test_find_ffmpeg_env_override(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "ffmpeg"
    fake.write_text("")
    monkeypatch.setenv("MISSION_DECK_FFMPEG", str(fake))
    assert find_ffmpeg() == str(fake)


def test_find_ffmpeg_bad_override_falls_through(monkeypatch) -> None:
    monkeypatch.setenv("MISSION_DECK_FFMPEG", "/nonexistent/ffmpeg")
    monkeypatch.setattr("mission_deck.stream.shutil.which", lambda _name: None)
    assert find_ffmpeg() is None


# --------------------------------------------------------------------------- #
# StreamWorker
# --------------------------------------------------------------------------- #
def test_worker_fails_cleanly_without_ffmpeg() -> None:
    worker = StreamWorker("rtsp://x/stream", ffmpeg="")
    worker.start()
    assert wait_for(lambda: worker.stats().state is StreamState.FAILED, 5)
    assert "ffmpeg" in worker.stats().last_error
    worker.stop()  # must stay FAILED, not flip to STOPPED
    assert worker.stats().state is StreamState.FAILED


def test_worker_goes_live_publishes_frames_and_stops(fake_ffmpeg, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_FRAMES", "3")
    monkeypatch.setenv("FAKE_THEN", "hang")
    worker = StreamWorker(
        "rtsp://fake/stream", ffmpeg=fake_ffmpeg,
        stall_seconds=30.0, connect_timeout=30.0,
    )
    worker.start()
    try:
        assert wait_for(
            lambda: worker.stats().state is StreamState.LIVE and worker.stats().frames >= 3
        )
        latest = worker.latest_frame()
        assert latest is not None
        seq, frame = latest
        assert seq >= 1
        assert len(frame) == FRAME_BYTES
        stats = worker.stats()
        assert stats.drops == 0
        assert stats.attempts == 1
        assert stats.connected_since is not None
    finally:
        worker.stop()
    assert wait_for(lambda: worker.stats().state is StreamState.STOPPED, 5)


def test_worker_detects_stall_and_reconnects(fake_ffmpeg, monkeypatch) -> None:
    # Each connect delivers 2 frames then goes silent: the watchdog must kill
    # the stalled process, count a drop, and the next attempt must go live.
    monkeypatch.setenv("FAKE_FRAMES", "2")
    monkeypatch.setenv("FAKE_THEN", "hang")
    worker = StreamWorker(
        "rtsp://fake/stream", ffmpeg=fake_ffmpeg,
        stall_seconds=0.6, connect_timeout=5.0, backoff=(0.1,),
    )
    worker.start()
    try:
        assert wait_for(lambda: worker.stats().drops >= 1, 20)
        assert wait_for(lambda: worker.stats().frames >= 4, 20)
        stats = worker.stats()
        assert stats.attempts >= 2
        assert "stalled" in stats.last_error or stats.state is StreamState.LIVE
    finally:
        worker.stop()


def test_worker_reconnects_when_process_exits(fake_ffmpeg, monkeypatch) -> None:
    # The camera closing the connection (EOF) must also trigger a reconnect.
    monkeypatch.setenv("FAKE_FRAMES", "2")
    monkeypatch.setenv("FAKE_THEN", "exit")
    worker = StreamWorker(
        "rtsp://fake/stream", ffmpeg=fake_ffmpeg,
        stall_seconds=30.0, connect_timeout=5.0, backoff=(0.1,),
    )
    worker.start()
    try:
        assert wait_for(lambda: worker.stats().drops >= 2, 20)
        assert worker.stats().frames >= 4
    finally:
        worker.stop()
