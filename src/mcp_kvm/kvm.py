"""Keyboard and pointer actions on the target, in screenshot coordinates."""
from __future__ import annotations

import time

from PIL import Image

from .capture import Capture
from .keymap import KEY_NUMLOCK, KEYPAD_DIGITS, LAYOUTS, MOD_LALT, MODIFIERS, parse_combo
from .link import Link
from .pointer import Pointer

BUTTONS = {"left": 0x01, "right": 0x02, "middle": 0x04}
STROKES_PER_LINE = 60


class Kvm:
    def __init__(self, link: Link, capture: Capture, layout: str = "us",
                 screenshot_width: int = 1280, target_os: str = "windows", pointer: str = "auto"):
        if layout not in LAYOUTS:
            raise ValueError(f"unknown keyboard layout {layout!r}; available: {', '.join(LAYOUTS)}")
        self.link = link
        self.capture = capture
        self.layout = LAYOUTS[layout]
        self.target_os = target_os
        # Every coordinate the tools take or show is in this space: the
        # captured frame scaled down to the screenshot width.
        cw, ch = capture.size
        width = min(screenshot_width, cw)
        self.screen = (width, round(ch * width / cw))
        self._last_input = 0.0  # time.monotonic() when the last action finished
        self.pointer = Pointer(self, pointer)

    # -- screen -------------------------------------------------------------

    def _fresh_frame(self) -> Image.Image:
        """A frame that already shows the effect of the last action."""
        frame, _ = self.capture.frame(after=self._last_input + self.capture.latency)
        return frame

    def screenshot(self, settle: bool = False) -> tuple[Image.Image, bool]:
        """Screenshot in screen coordinates. With `settle`, waits for the screen to
        stop changing after the last action; the flag is False if it never did."""
        if settle:
            frame, settled = self.capture.settled_frame(self._last_input)
        else:
            frame, settled = self._fresh_frame(), True
        return frame.resize(self.screen, Image.LANCZOS), settled

    def zoom(self, x: int, y: int, width: int, height: int) -> Image.Image:
        """The given screen region at the full resolution of the capture."""
        self._check_point(x, y)
        if width < 1 or height < 1:
            raise ValueError("zoom region must be at least 1x1")
        frame = self._fresh_frame()
        sx = frame.width / self.screen[0]
        sy = frame.height / self.screen[1]
        box = (round(x * sx), round(y * sy),
               min(frame.width, round((x + width) * sx)), min(frame.height, round((y + height) * sy)))
        crop = frame.crop(box)
        if crop.width > self.screen[0]:
            crop = crop.resize((self.screen[0], round(crop.height * self.screen[0] / crop.width)), Image.LANCZOS)
        return crop

    # -- pointer ------------------------------------------------------------
    # Which mouse the target accepts, and how to reach a point with it, is the
    # business of pointer.py. Here a point is just a point.

    def _check_point(self, x: int, y: int) -> None:
        w, h = self.screen
        if not (0 <= x < w and 0 <= y < h):
            raise ValueError(f"point ({x}, {y}) is outside the {w}x{h} screen")

    def _sync(self) -> None:
        # The board answers when everything is sent, or by itself after the
        # target has taken nothing for 5 s. The wait here only has to outlast
        # the slowest target: a boot loader takes a few keys per second.
        try:
            self.link.command("sync", timeout=120.0)
        finally:
            self._last_input = time.monotonic()

    def _mods(self, modifiers: str) -> int:
        mods = 0
        for name in filter(None, modifiers.lower().replace(" ", "").split("+")):
            if name not in MODIFIERS:
                raise ValueError(f"unknown modifier {name!r}; use ctrl, shift, alt or win")
            mods |= MODIFIERS[name]
        return mods

    def move(self, x: int, y: int) -> None:
        self._check_point(x, y)
        self.pointer.goto(x, y)
        self._sync()

    def click(self, x: int, y: int, button: str = "left", count: int = 1, modifiers: str = "") -> None:
        if button not in BUTTONS:
            raise ValueError(f"unknown button {button!r}; use left, right or middle")
        if not 1 <= count <= 3:
            raise ValueError("count must be 1, 2 or 3")
        self._check_point(x, y)
        mods = self._mods(modifiers)
        try:
            self.pointer.goto(x, y)
            if mods:
                self.link.command(f"k {mods:02x}")
            time.sleep(0.04)  # let the target register the hover before the press
            for i in range(count):
                if i:
                    time.sleep(0.07)
                self.pointer.press(BUTTONS[button])
                time.sleep(0.04)
                self.pointer.press(0)
        finally:
            if mods:
                self.link.command("k")
        self._sync()

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left") -> None:
        if button not in BUTTONS:
            raise ValueError(f"unknown button {button!r}; use left, right or middle")
        self._check_point(x1, y1)
        self._check_point(x2, y2)
        b = BUTTONS[button]
        self.pointer.goto(x1, y1)
        time.sleep(0.05)
        try:
            self.pointer.press(b)
            time.sleep(0.1)
            steps = 16
            for i in range(1, steps + 1):
                self.pointer.goto(round(x1 + (x2 - x1) * i / steps), round(y1 + (y2 - y1) * i / steps), held=b)
                time.sleep(0.02)
            time.sleep(0.1)
        finally:
            self.link.command("r")
        self._sync()

    def scroll(self, x: int, y: int, direction: str, amount: int = 3) -> None:
        steps = {"up": (1, 0), "down": (-1, 0), "right": (0, 1), "left": (0, -1)}
        if direction not in steps:
            raise ValueError(f"unknown direction {direction!r}; use up, down, left or right")
        if not 1 <= amount <= 100:
            raise ValueError("amount must be between 1 and 100 notches")
        self._check_point(x, y)
        dy, dx = steps[direction]
        self.pointer.goto(x, y)
        time.sleep(0.04)
        for _ in range(amount):
            self.pointer.wheel(dy, dx)
            time.sleep(0.015)
        self._sync()

    # -- keyboard -----------------------------------------------------------

    def key(self, combo: str, repeat: int = 1, hold: float = 0.0) -> None:
        if not 1 <= repeat <= 100:
            raise ValueError("repeat must be between 1 and 100")
        mods, keys = parse_combo(combo, self.layout)
        pressed = f"k {mods:02x} " + " ".join(f"{k:02x}" for k in keys)
        try:
            for _ in range(repeat):
                if mods and keys:
                    self.link.command(f"k {mods:02x}")
                self.link.command(pressed.strip())
                self._sync()
                time.sleep(max(hold, 0.03))
                if mods and keys:
                    self.link.command(f"k {mods:02x}")
                self.link.command("k")
        finally:
            self.link.command("k")
        self._sync()

    def type_text(self, text: str) -> None:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        unknown = sorted({c for c in text if c not in self.layout and not self._alt_digits(c)})
        if unknown:
            raise ValueError("cannot type these characters with the target's keyboard layout: "
                             + " ".join(repr(c) for c in unknown))
        strokes: list[tuple[int, int]] = []
        for c in text:
            if c in self.layout:
                strokes.append(self.layout[c])
            else:
                self._send_strokes(strokes)
                strokes = []
                self._alt_code(c)
        self._send_strokes(strokes)

    def _send_strokes(self, strokes: list[tuple[int, int]]) -> None:
        for i in range(0, len(strokes), STROKES_PER_LINE):
            chunk = strokes[i:i + STROKES_PER_LINE]
            self.link.command("t " + " ".join(f"{m:02x}{k:02x}" for m, k in chunk))
            self._sync()

    def _alt_digits(self, c: str) -> str | None:
        """Digits of the Windows Alt code for a character outside the layout."""
        if self.target_os != "windows":
            return None
        try:
            return "0" + str(c.encode("cp1252")[0])
        except UnicodeEncodeError:
            return None

    def _alt_code(self, c: str) -> None:
        # Alt codes are read from the keypad and need Num Lock on.
        numlock_was_off = not self.status()["leds"] & 0x01
        if numlock_was_off:
            self.link.command(f"t 00{KEY_NUMLOCK:02x}")
        try:
            self.link.command(f"k {MOD_LALT:02x}")
            for d in self._alt_digits(c):
                self.link.command(f"k {MOD_LALT:02x} {KEYPAD_DIGITS[d]:02x}")
                self.link.command(f"k {MOD_LALT:02x}")
        finally:
            self.link.command("k")
        if numlock_was_off:
            self.link.command(f"t 00{KEY_NUMLOCK:02x}")
        self._sync()

    # -- state --------------------------------------------------------------

    def status(self) -> dict:
        fields = dict(part.split("=", 1) for part in self.link.command("info").split())
        return {
            "firmware": fields.get("ver", "?"),
            "usb_mounted": fields.get("mounted") == "1",
            "usb_suspended": fields.get("suspended") == "1",
            "leds": int(fields.get("leds", "0"), 16),
            "raw": fields,
        }

    def release_all(self) -> None:
        self.link.command("r")
