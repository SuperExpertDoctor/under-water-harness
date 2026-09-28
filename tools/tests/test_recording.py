import asyncio
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest

import uuv_game.recording as recording
from uuv_game.api import create_app
from uuv_game.recording import RecordingError, RecordingManager


class FakeVideo:
    def __init__(self, location):
        self.location = location

    async def path(self):
        return str(self.location)


class FakePage:
    def __init__(self, location):
        self.video = FakeVideo(location)
        self.visited = None

    async def goto(self, url, **_options):
        self.visited = url

    async def wait_for_selector(self, *_args, **_options):
        return True


class FakeContext:
    def __init__(self, directory):
        self.location = Path(directory) / "browser.webm"
        self.page = FakePage(self.location)

    async def new_page(self):
        return self.page

    async def close(self):
        self.location.write_bytes(b"recorded frame")


class FakeBrowser:
    async def new_context(self, **options):
        self.context = FakeContext(options["record_video_dir"])
        return self.context

    async def close(self):
        pass


class FakePlaywright:
    def __init__(self):
        self.browser = FakeBrowser()
        self.chromium = self

    async def start(self):
        return self

    async def launch(self, **_options):
        return self.browser

    async def stop(self):
        pass


@pytest.mark.asyncio
async def test_recording_stays_active_without_operator_tab_and_saves_mp4(tmp_path, monkeypatch):
    fake = FakePlaywright()
    monkeypatch.setattr(recording, "async_playwright", lambda: fake)
    monkeypatch.setattr(recording.shutil, "which", lambda executable: "/usr/bin/ffmpeg")

    async def encode(*args, **_kwargs):
        Path(args[-1]).write_bytes(b"encoded video")

        class Process:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return Process()

    monkeypatch.setattr(recording.asyncio, "create_subprocess_exec", encode)
    manager = RecordingManager("http://127.0.0.1:5173", tmp_path)
    started = await manager.start()
    assert started["status"] == "recording"
    assert fake.browser.context.page.visited == "http://127.0.0.1:5173"
    with pytest.raises(RecordingError, match="recording_active"):
        await manager.start()
    assert manager.status()["status"] == "recording"
    assert (await manager.stop())["status"] == "finalizing"
    for _ in range(100):
        if manager.status()["status"] != "finalizing":
            break
        await asyncio.sleep(.01)
    result = manager.status()
    assert result["status"] == "completed"
    assert (tmp_path / result["filename"]).read_bytes() == b"encoded video"
    assert not list(tmp_path.glob("*.webm"))
    assert (await manager.start())["status"] == "recording"
    await manager.close()


@pytest.mark.asyncio
async def test_encoding_failure_is_visible_and_preserves_source(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "async_playwright", FakePlaywright)
    monkeypatch.setattr(recording.shutil, "which", lambda executable: "/usr/bin/ffmpeg")

    async def fail_encode(*_args, **_kwargs):
        class Process:
            returncode = 1

            async def communicate(self):
                return b"", b"encoder failed"

        return Process()

    monkeypatch.setattr(recording.asyncio, "create_subprocess_exec", fail_encode)
    manager = RecordingManager("http://127.0.0.1:5173", tmp_path)
    await manager.start()
    await manager.stop()
    for _ in range(100):
        if manager.status()["status"] != "finalizing":
            break
        await asyncio.sleep(.01)
    assert manager.status()["status"] == "failed"
    assert "encoder failed" in manager.status()["error"]
    assert manager.status()["filename"] is None
    assert len(list(tmp_path.glob("*.webm"))) == 1
    await manager.close()


@pytest.mark.asyncio
async def test_output_directory_failure_has_explicit_failed_state(tmp_path, monkeypatch):
    monkeypatch.setattr(recording.shutil, "which", lambda executable: "/usr/bin/ffmpeg")
    manager = RecordingManager("http://127.0.0.1:5173", tmp_path / "no-permission")

    def deny(*_args, **_kwargs):
        raise PermissionError("output directory is read-only")

    monkeypatch.setattr(recording.Path, "mkdir", deny)
    with pytest.raises(RecordingError, match="output_unavailable"):
        await manager.start()
    assert manager.status()["status"] == "failed"
    assert "read-only" in manager.status()["error"]


def test_recording_endpoints_require_operator_session_and_accept_no_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(recording, "async_playwright", FakePlaywright)
    monkeypatch.setattr(recording.shutil, "which", lambda executable: "/usr/bin/ffmpeg")

    async def encode(*args, **_kwargs):
        Path(args[-1]).write_bytes(b"encoded video")

        class Process:
            returncode = 0

            async def communicate(self):
                return b"", b""

        return Process()

    monkeypatch.setattr(recording.asyncio, "create_subprocess_exec", encode)
    app = create_app(tmp_path / "runtime.sqlite", ticking=False, ui_url="http://127.0.0.1:5173", recording_dir=tmp_path / "outputs")
    with TestClient(app) as client:
        assert client.get("/api/recording").json()["status"] == "idle"
        assert client.post("/api/recording/start", json={}).status_code == 403
        client.get("/api/health")
        assert client.post("/api/recording/start", json={"output_path": "/tmp/other.mp4"}).status_code == 422
        assert client.get("/api/recording").json()["status"] == "idle"
        assert client.post("/api/recording/start", json={}).json()["status"] == "recording"
        assert client.post("/api/recording/start", json={}).status_code == 409
        assert client.post("/api/recording/stop", json={"output_path": "/tmp/other.mp4"}).status_code == 422
        assert client.post("/api/recording/stop", json={}).json()["status"] == "finalizing"
        for _ in range(100):
            if client.get("/api/recording").json()["status"] == "completed":
                break
            time.sleep(.01)
        assert client.get("/api/recording").json()["filename"].endswith(".mp4")
