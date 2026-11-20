"""Command-line access to the same actions as the MCP tools, for testing.

    python -m mcp_kvm.cli shot out.jpg
    python -m mcp_kvm.cli raw "info"
    python -m mcp_kvm.cli click 640 360 [button] [count]
    python -m mcp_kvm.cli move 640 360
    python -m mcp_kvm.cli type "hello"
    python -m mcp_kvm.cli key ctrl+a
    python -m mcp_kvm.cli scroll 640 360 down 3
    python -m mcp_kvm.cli drag 100 100 300 300

Action commands accept a trailing "--shot file.jpg" to save the settled screen.
"""
from __future__ import annotations

import sys
import time

from .config import build_kvm


def main() -> None:
    args = sys.argv[1:]
    shot = None
    if "--shot" in args:
        i = args.index("--shot")
        shot = args[i + 1]
        del args[i:i + 2]
    if not args:
        sys.exit(__doc__)
    cmd, rest = args[0], args[1:]
    kvm = build_kvm()
    started = time.monotonic()
    if cmd == "shot":
        image, _ = kvm.screenshot()
        image.save(rest[0], quality=88)
        print(f"{image.width}x{image.height} -> {rest[0]}")
        return
    if cmd == "raw":
        print(kvm.link.command(" ".join(rest)))
    elif cmd == "status":
        print(kvm.status())
    elif cmd == "click":
        kvm.click(int(rest[0]), int(rest[1]), *(rest[2:3] or ["left"]), int((rest[3:4] or ["1"])[0]))
    elif cmd == "move":
        kvm.move(int(rest[0]), int(rest[1]))
    elif cmd == "type":
        kvm.type_text(rest[0])
    elif cmd == "key":
        kvm.key(rest[0], int((rest[1:2] or ["1"])[0]))
    elif cmd == "scroll":
        kvm.scroll(int(rest[0]), int(rest[1]), rest[2], int((rest[3:4] or ["3"])[0]))
    elif cmd == "drag":
        kvm.drag(*(int(v) for v in rest[:4]))
    else:
        sys.exit(__doc__)
    print(f"done in {time.monotonic() - started:.2f} s")
    if shot:
        image, settled = kvm.screenshot(settle=True)
        image.save(shot, quality=88)
        print(f"{image.width}x{image.height} -> {shot} (settled={settled})")


if __name__ == "__main__":
    main()
