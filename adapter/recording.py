"""Server-owned recording of the live operator dashboard."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import secrets
import shutil
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright


class RecordingError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code = code
        self.status = status


class RecordingManager:
    def __init__(self, ui_url, output_dir):
        self.ui_url = ui_url
        self.output_dir = Path(output_dir)
        self.state = "idle"
        self.error = None
        self.filename = None
        self.started_at = None
        self.duration_s = 0
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.task = None
        self.lock = asyncio.Lock()

    def status(self):
        elapsed = time.monotonic() - self.started_at if self.started_at is not None and self.state in ("starting", "recording") else self.duration_s
        return {"status": self.state, "elapsed_s": round(max(0, elapsed), 1),
                "filename": self.filename, "error": self.error}

    async def _release(self):
        for resource in (self.context, self.browser, self.playwright):
            if resource is not None:
                try:
                    await (resource.stop() if resource is self.playwright else resource.close())
                except Exception:
                    pass
        self.context = self.browser = self.playwright = self.page = None

    async def start(self):
        async with self.lock:
            if self.state in ("starting", "recording", "finalizing"):
                raise RecordingError("recording_active")
            parsed = urlparse(self.ui_url or "")
            if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1") or not parsed.port:
                raise RecordingError("local_ui_url_required", 503)
            if shutil.which("ffmpeg") is None:
                raise RecordingError("ffmpeg_unavailable", 503)
            self.state, self.error, self.filename = "starting", None, None
            self.duration_s = 0
            self.started_at = time.monotonic()
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                self.state, self.error = "failed", f"output_unavailable: {str(exc)[:200]}"
                self.started_at = None
                raise RecordingError("output_unavailable", 503) from exc
            try:
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=True)
                self.context = await self.browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    record_video_dir=str(self.output_dir),
                    record_video_size={"width": 1440, "height": 900},
                )
                self.page = await self.context.new_page()
                await self.page.goto(self.ui_url, wait_until="domcontentloaded", timeout=15000)
                await self.page.wait_for_selector(".app-layout", timeout=15000)
                self.state = "recording"
                return self.status()
            except Exception as exc:
                self.state, self.error = "failed", f"browser_start_failed: {str(exc)[:200]}"
                self.started_at = None
                await self._release()
                raise RecordingError("browser_start_failed", 503) from exc

    async def stop(self):
        async with self.lock:
            if self.state != "recording":
                raise RecordingError("recording_not_active")
            self.duration_s = round(time.monotonic() - self.started_at, 1)
            self.started_at = None
            self.state = "finalizing"
            self.task = asyncio.create_task(self._finalize())
            return self.status()

    async def _finalize(self):
        name = f"mission-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}"
        source = self.output_dir / f"{name}.webm"
        temporary = self.output_dir / f"{name}.partial.mp4"
        dest = self.output_dir / f"{name}.mp4"
        try:
            video = self.page.video
            await self.context.close()
            self.context = None
            browser_path = Path(await video.path())
            browser_path.replace(source)
            await self._release()
            process = await asyncio.create_subprocess_exec(
                shutil.which("ffmpeg"), "-nostdin", "-loglevel", "error", "-i", str(source),
                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(temporary), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await process.communicate()
            if process.returncode != 0 or not temporary.exists() or temporary.stat().st_size == 0:
                raise RuntimeError(f"encoding_failed: {stderr.decode(errors='replace')[:200]}")
            temporary.replace(dest)
            source.unlink()
            self.filename, self.state, self.error = dest.name, "completed", None
        except Exception as exc:
            self.state, self.error = "failed", str(exc)[:240]
            if temporary.exists():
                temporary.unlink()
            await self._release()

    async def close(self):
        if self.state == "recording":
            await self.stop()
        if self.task is not None:
            await self.task
