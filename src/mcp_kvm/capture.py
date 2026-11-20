"""Reads the target's screen from an HDMI-to-USB capture dongle (a UVC camera).

Two sources share one interface:

- HelperCapture (macOS): the native `kvm-capture` helper, built by
  tools/build-capture.sh. It uses the dongle's fast compressed mode.
- FfmpegCapture: PyAV, on any system. On macOS it is limited to the dongle's
  uncompressed mode, about 5 frames/s.

Both start on first use and stop after a period without requests, so the
camera indicator is not lit while nobody is looking.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageChops

IDLE_STOP_S = 120.0
HELPER_PATH = Path(__file__).parent / "_bin" / "kvm-capture"


class CaptureError(RuntimeError):
    pass


class Capture:
    """Interface of a screen source. `latency` is the time between an input
    reaching the target and the frame that shows it being available here."""

    device: str
    size: tuple[int, int]
    latency: float
    description: str

    def frame(self, after: float = 0.0, timeout: float = 5.0) -> tuple[Image.Image, float]:
        """A frame that arrived later than `after` (a time.monotonic() value),
        with its arrival time."""
        raise NotImplementedError

    def settled_frame(self, since: float, max_wait: float = 3.0,
                      tolerance: float = 0.4) -> tuple[Image.Image, bool]:
        """Frame taken once the screen stopped changing after an input sent at `since`.

        Skips the frames that predate the input, then returns once the screen
        has not changed for `still_for` seconds, or after `max_wait`. The flag
        is False if the screen was still changing.
        """
        still_for = 0.2
        deadline = since + max_wait
        previous, when = self.frame(after=since + self.latency)
        still_since = when
        while time.monotonic() < deadline:
            try:
                current, when = self.frame(after=when + 0.03,
                                           timeout=max(0.05, deadline - time.monotonic()))
            except CaptureError:
                break
            if _difference(previous, current) > tolerance:
                still_since = when
            elif when - still_since >= still_for:
                return current, True
            previous = current
        return previous, False


def _difference(a: Image.Image, b: Image.Image) -> float:
    """Mean absolute difference of two frames on a small grey thumbnail (0..255)."""
    small = (192, 108)
    diff = ImageChops.difference(a.convert("L").resize(small, Image.BILINEAR),
                                 b.convert("L").resize(small, Image.BILINEAR))
    histogram = diff.histogram()
    return sum(value * count for value, count in enumerate(histogram)) / (small[0] * small[1])


class HelperCapture(Capture):
    # Measured from a pointer command to the frame showing it: see README.
    latency = 0.25

    def __init__(self, device: str, size: tuple[int, int] = (1920, 1080), fps: int = 30):
        self.device = device
        self.size = size
        self.fps = fps
        self.description = "native helper"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._watchdog: threading.Thread | None = None

    def _start(self) -> None:
        proc = subprocess.Popen(
            [str(HELPER_PATH), self.device, str(self.size[0]), str(self.size[1]), str(self.fps)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        ready = proc.stdout.readline().decode("utf-8", "replace").strip()
        if not ready.startswith("R "):
            proc.kill()
            raise CaptureError(ready[2:] if ready.startswith("E ") else f"capture helper failed to start: {ready!r}")
        self.description = f"native helper, {ready[2:]}"
        self._proc = proc
        if self._watchdog is None:
            self._watchdog = threading.Thread(target=self._stop_when_idle, name="kvm-capture-idle", daemon=True)
            self._watchdog.start()

    def _stop(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
            self._proc = None

    def _stop_when_idle(self) -> None:
        while True:
            time.sleep(5)
            with self._lock:
                if self._proc is not None and time.monotonic() - self._last_request > IDLE_STOP_S:
                    self._stop()

    def frame(self, after: float = 0.0, timeout: float = 5.0) -> tuple[Image.Image, float]:
        wait = after - time.monotonic()
        if wait > timeout:
            raise CaptureError(f"no frame from capture device {self.device!r} within {timeout:.0f} s")
        if wait > 0:
            time.sleep(wait)
        with self._lock:
            self._last_request = time.monotonic()
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            # Accept a frame that arrived after `after`; ask for a new one otherwise.
            max_age_ms = max(0, int((time.monotonic() - after) * 1000) - 1)
            try:
                self._proc.stdin.write(f"g {min(max_age_ms, 1000)}\n".encode())
                self._proc.stdin.flush()
                head = self._proc.stdout.readline().decode("utf-8", "replace").split()
                if not head or head[0] != "F":
                    raise CaptureError("capture helper: " + (" ".join(head[1:]) or "exited"))
                age_us, width, height, stride, length = (int(v) for v in head[1:])
                data = self._proc.stdout.read(length)
                if len(data) != length:
                    raise CaptureError("capture helper exited in the middle of a frame")
            except (OSError, ValueError, CaptureError) as e:
                self._stop()
                raise e if isinstance(e, CaptureError) else CaptureError(f"capture helper failed: {e}") from e
            when = time.monotonic() - age_us / 1e6
        image = Image.frombuffer("RGBA", (width, height), data, "raw", "BGRA", stride, 1).convert("RGB")
        return image, when


def _open_ffmpeg(device: str, size: tuple[int, int]):
    import av
    video_size = f"{size[0]}x{size[1]}"
    if sys.platform == "darwin":
        # ffmpeg's avfoundation input cannot choose among formats of the same
        # size and lands on the dongle's uncompressed mode (lossless, 5 frames/s).
        return av.open(f"{device}:none", format="avfoundation",
                       options={"framerate": "30", "video_size": video_size, "pixel_format": "nv12"})
    if sys.platform.startswith("linux"):
        return av.open(device, format="v4l2", options={"video_size": video_size, "input_format": "mjpeg"})
    return av.open(f"video={device}", format="dshow", options={"video_size": video_size})


class FfmpegCapture(Capture):
    # Measured on macOS at 0.49 to 0.62 s with the 5 frames/s mode.
    latency = 0.7

    def __init__(self, device: str, size: tuple[int, int] = (1920, 1080)):
        self.device = device
        self.size = size
        self.description = "ffmpeg"
        self._cond = threading.Condition()
        self._frame = None
        self._frame_time = 0.0
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._last_request = 0.0

    def _run(self) -> None:
        import av
        container = None
        try:
            container = _open_ffmpeg(self.device, self.size)
            while time.monotonic() - self._last_request < IDLE_STOP_S:
                try:
                    for packet in container.demux(video=0):
                        for frame in packet.decode():
                            with self._cond:
                                self._frame = frame
                                self._frame_time = time.monotonic()
                                self._error = None
                                self._cond.notify_all()
                        break
                except av.error.BlockingIOError:
                    time.sleep(0.005)  # no new frame yet
        except Exception as e:  # device unplugged, permission denied, ...
            with self._cond:
                self._error = f"{type(e).__name__}: {e}"
                self._cond.notify_all()
        finally:
            if container is not None:
                container.close()
            with self._cond:
                self._frame = None
                self._thread = None
                self._cond.notify_all()

    def frame(self, after: float = 0.0, timeout: float = 5.0) -> tuple[Image.Image, float]:
        self._last_request = time.monotonic()
        deadline = time.monotonic() + timeout
        with self._cond:
            if self._thread is None:
                self._error = None
                self._thread = threading.Thread(target=self._run, name="kvm-capture", daemon=True)
                self._thread.start()
            while self._frame is None or self._frame_time <= after:
                if self._error:
                    raise CaptureError(f"cannot read capture device {self.device!r}: {self._error}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CaptureError(f"no frame from capture device {self.device!r} within {timeout:.0f} s")
                self._cond.wait(remaining)
            frame, when = self._frame, self._frame_time
        return frame.to_image(), when


def open_capture(device: str, size: tuple[int, int], backend: str = "auto") -> Capture:
    """backend: "native" (macOS helper), "ffmpeg", or "auto" (native when built)."""
    if backend == "native" or (backend == "auto" and sys.platform == "darwin" and HELPER_PATH.exists()):
        if not HELPER_PATH.exists():
            raise CaptureError(f"capture helper not built: run tools/build-capture.sh ({HELPER_PATH} is missing)")
        return HelperCapture(device, size)
    if backend not in ("auto", "ffmpeg"):
        raise ValueError(f"unknown capture backend {backend!r}; use auto, native or ffmpeg")
    return FfmpegCapture(device, size)
