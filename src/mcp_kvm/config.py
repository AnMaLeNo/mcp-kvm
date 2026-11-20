"""Settings, read from environment variables."""
from __future__ import annotations

import os

from .capture import open_capture
from .kvm import Kvm
from .link import open_link


def build_kvm() -> Kvm:
    """
    KVM_CAPTURE_DEVICE   name of the HDMI capture dongle     (default "USB3. 0 capture")
    KVM_CAPTURE_SIZE     frame size asked from the dongle    (default 1920x1080)
    KVM_CAPTURE_BACKEND  "native", "ffmpeg" or "auto"         (default auto)
    KVM_LINK             how to reach the board: "serial", "serial:<port>" or
                         "ssh:<user@host>"                   (default: local serial port
                         if a board is plugged in here, else SSH to KVM_SSH)
    KVM_SSH              user@host of the target, for the SSH link
    KVM_LAYOUT           keyboard layout set on the target   (default us)
    KVM_TARGET_OS        target system, enables Alt codes    (default windows)
    KVM_SCREENSHOT_WIDTH width of screenshots, in pixels     (default 1280)
    KVM_POINTER          "auto", "absolute" or "relative"    (default auto)
    """
    width, height = os.environ.get("KVM_CAPTURE_SIZE", "1920x1080").lower().split("x")
    capture = open_capture(os.environ.get("KVM_CAPTURE_DEVICE", "USB3. 0 capture"), (int(width), int(height)),
                           os.environ.get("KVM_CAPTURE_BACKEND", "auto"))
    return Kvm(
        open_link(os.environ.get("KVM_LINK"), os.environ.get("KVM_SSH")),
        capture,
        layout=os.environ.get("KVM_LAYOUT", "us"),
        screenshot_width=int(os.environ.get("KVM_SCREENSHOT_WIDTH", "1280")),
        target_os=os.environ.get("KVM_TARGET_OS", "windows"),
        pointer=os.environ.get("KVM_POINTER", "auto"),
    )
