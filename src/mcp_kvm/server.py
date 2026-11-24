"""MCP server: lets an agent see and operate a computer through its HDMI
output and a USB keyboard and pointer, like a person sitting in front of it."""
from __future__ import annotations

import asyncio
import io

from mcp.server.mcpserver import Image, MCPServer

from .config import build_kvm
from .kvm import WAKE_CHECKS, Kvm

mcp = MCPServer(
    "kvm",
    instructions=(
        "Controls a physical computer: the screen comes from its HDMI output, input goes through "
        "a USB keyboard and pointer. Start with `screenshot`. All coordinates are pixels of the "
        "screenshot, origin at the top left. Actions return a new screenshot taken once the "
        "screen has settled; use `zoom` to read small text. The same tools work before an "
        "operating system is running (firmware setup, boot menu); a text-only screen has no "
        "mouse, use the keyboard there. Pointer tools report which mouse they used: the "
        "absolute pointer, or the relative mouse where only that one is accepted (it is slower "
        "and has no wheel). If the cursor does not land on the point, call `recalibrate_pointer`. "
        "A boot loader such as GRUB may draw the result of a key several seconds late: take "
        "another screenshot before acting on what the screen shows, and press a key early to "
        "stop its countdown."
    ),
)

_kvm: Kvm | None = None
_lock = asyncio.Lock()  # one action at a time on the target


def _get() -> Kvm:
    global _kvm
    if _kvm is None:
        _kvm = build_kvm()
    return _kvm


def _jpeg(image) -> Image:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    return Image(data=buffer.getvalue(), format="jpeg")


async def _act(description: str, action, screenshot: bool, pointer: bool = False) -> list:
    """Runs a blocking action off the event loop, then reports the resulting screen.
    For a pointer action, the report says which mouse carried it out."""
    async with _lock:
        kvm = _get()
        await asyncio.to_thread(action, kvm)
        if pointer:
            description += f" Mouse used: {kvm.pointer.describe()}."
        if not screenshot:
            return [description]
        image, settled = await asyncio.to_thread(kvm.screenshot, True)
    note = "" if settled else " The screen was still changing when this was taken."
    return [f"{description}{note}", _jpeg(image)]


@mcp.tool(structured_output=False)
async def screenshot() -> list:
    """Current screen of the target. Coordinates used by every other tool are pixels of this image.
    An all-black screen is taken for a display gone to sleep: Shift is pressed and the screen
    is watched for a few seconds. The result says when that happened, and comes without an
    image if the screen stayed black."""
    async with _lock:
        kvm = _get()
        image, woken = await asyncio.to_thread(kvm.screenshot_awake)
    if image is None:
        return [f"No picture: the screen is all black, and stayed black for {WAKE_CHECKS} s after "
                "Shift was pressed to wake it. The target may be off, in deep sleep, or really "
                "showing a black screen."]
    note = " It was all black: Shift was pressed to wake the display." if woken else ""
    return [f"Screen is {image.width}x{image.height}.{note}", _jpeg(image)]


@mcp.tool(structured_output=False)
async def zoom(x: int, y: int, width: int, height: int) -> list:
    """A region of the screen at full capture resolution, to read small text.
    (x, y) is the region's top-left corner in screenshot pixels."""
    async with _lock:
        kvm = _get()
        image = await asyncio.to_thread(kvm.zoom, x, y, width, height)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return [f"Region {width}x{height} at ({x}, {y}).", Image(data=buffer.getvalue(), format="png")]


@mcp.tool(structured_output=False)
async def click(x: int, y: int, button: str = "left", count: int = 1, modifiers: str = "",
                screenshot: bool = True) -> list:
    """Click at a point. button: left, right or middle. count: 2 for a double click.
    modifiers: keys held during the click, e.g. "ctrl" or "ctrl+shift"."""
    what = {1: "Clicked", 2: "Double-clicked", 3: "Triple-clicked"}.get(count, "Clicked")
    return await _act(f"{what} {button} at ({x}, {y}).",
                      lambda k: k.click(x, y, button, count, modifiers), screenshot, pointer=True)


@mcp.tool(structured_output=False)
async def move(x: int, y: int, screenshot: bool = True) -> list:
    """Move the pointer to a point without clicking, e.g. to reveal a tooltip or a hover menu."""
    return await _act(f"Moved to ({x}, {y}).", lambda k: k.move(x, y), screenshot, pointer=True)


@mcp.tool(structured_output=False)
async def drag(from_x: int, from_y: int, to_x: int, to_y: int, button: str = "left",
               screenshot: bool = True) -> list:
    """Press at one point, move to another, release."""
    return await _act(f"Dragged from ({from_x}, {from_y}) to ({to_x}, {to_y}).",
                      lambda k: k.drag(from_x, from_y, to_x, to_y, button), screenshot, pointer=True)


@mcp.tool(structured_output=False)
async def scroll(x: int, y: int, direction: str, amount: int = 3, screenshot: bool = True) -> list:
    """Turn the wheel with the pointer at a point. direction: up, down, left or right.
    amount: wheel notches."""
    return await _act(f"Scrolled {direction} {amount} at ({x}, {y}).",
                      lambda k: k.scroll(x, y, direction, amount), screenshot, pointer=True)


@mcp.tool(structured_output=False)
async def type_text(text: str, screenshot: bool = True) -> list:
    """Type text at the keyboard focus. A newline presses Enter, a tab presses Tab."""
    return await _act(f"Typed {len(text)} characters.", lambda k: k.type_text(text), screenshot)


@mcp.tool(structured_output=False)
async def key(keys: str, repeat: int = 1, hold_seconds: float = 0.0, screenshot: bool = True) -> list:
    """Press a key or a combination, e.g. "enter", "ctrl+c", "alt+tab", "win+r", "ctrl+shift+esc", "f5".
    Names: enter, esc, tab, space, backspace, delete, insert, home, end, pageup, pagedown,
    up, down, left, right, f1..f24, capslock, printscreen, menu; modifiers ctrl, shift, alt, win."""
    return await _act(f"Pressed {keys}" + (f" x{repeat}." if repeat > 1 else "."),
                      lambda k: k.key(keys, repeat, hold_seconds), screenshot)


@mcp.tool()
async def status() -> dict:
    """State of the link to the target: firmware version, USB state, keyboard LEDs, screen size,
    and which mouse the target accepts (found automatically on the first pointer action)."""
    async with _lock:
        kvm = _get()
        info = await asyncio.to_thread(kvm.status)
    leds = info["leds"]
    return {
        "firmware": info["firmware"],
        "usb_mounted": info["usb_mounted"],
        "usb_suspended": info["usb_suspended"],
        "num_lock": bool(leds & 1),
        "caps_lock": bool(leds & 2),
        "screen": {"width": kvm.screen[0], "height": kvm.screen[1]},
        "pointer": kvm.pointer.describe(after_action=False),
    }


@mcp.tool(structured_output=False)
async def recalibrate_pointer(screenshot: bool = True) -> list:
    """Test again, now, which mouse the target accepts and how far it moves the cursor.
    Use it when clicks land beside the point, when the cursor did not follow, or after the
    screen changed kind (firmware setup, boot menu, operating system) without a restart;
    a restart of the target is detected by itself. The test moves the cursor: with the
    relative mouse it visits the top-left corner and a few points, and takes a few seconds.
    It ends where the cursor was last sent, or at the centre of the screen. Nothing is clicked."""
    return await _act("Pointer recalibrated.", lambda k: k.pointer.recalibrate(), screenshot, pointer=True)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
