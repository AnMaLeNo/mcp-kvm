"""Puts the target's cursor on a screen point, whatever mouse the target accepts.

The target board offers two mice:

- an absolute pointer, which is told the point directly. Operating systems
  accept it; firmware setup screens ignore it.
- a relative mouse, which is told how far to move. Firmware setup screens
  accept it. A point is reached by pushing the cursor into the top-left corner
  and moving a known distance from there.

Which one works is found by looking at the screen, not by asking the caller:
this module moves the cursor a few pixels and back and compares the frames.
What changed and then changed back is the cursor, so its position is known
without any model of what a cursor looks like. The same trick measures how
many pixels one count of the relative mouse is worth, and corrects the
remaining error before a click.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .kvm import Kvm

POINTER_MAX = 32767
NUDGE_PIXELS = 10   # absolute pointer: size of the test move
NUDGE_COUNTS = 12   # relative mouse: size of the test move
HOME_REPORTS = 40   # 40 x 127 counts up and left: past the edge of any screen
HOME_PAUSE_S = 0.15
ARRIVED_PIXELS = 2  # relative mouse: accepted distance to the requested point
CURSOR_BOX_PIXELS = 70  # largest box a cursor and its test move can cover
MAX_CORRECTIONS = 6


class PointerError(RuntimeError):
    pass


def _at_least_one(counts: float) -> int:
    """Rounds a correction, never to zero while there is an error to remove."""
    if abs(counts) < 0.3:
        return 0
    return round(counts) or (1 if counts > 0 else -1)


class Pointer:
    def __init__(self, kvm: "Kvm", forced: str = "auto"):
        if forced not in ("auto", "absolute", "relative"):
            raise ValueError(f"unknown pointer mode {forced!r}; use auto, absolute or relative")
        self.kvm = kvm
        self.forced = forced
        self.mode: str | None = None   # "absolute" or "relative" once decided
        self.seen = False              # the cursor was actually found on screen
        self._decided_for: str | None = None  # value of the board's "enum" when decided
        self.just_decided = False      # the mouse was chosen during the last action
        self.corrections = 0           # relative mouse: corrections made by the last move
        self._pos = (0, 0)
        # Relative mouse: screen pixel = offset + scale * counts from the corner.
        self._scale = (1.0, 1.0)
        self._offset = (0.0, 0.0)
        self._calibrated = False

    # -- seeing the cursor ----------------------------------------------------

    def _gray(self) -> np.ndarray:
        return np.asarray(self.kvm._fresh_frame().convert("L"), dtype=np.int16)

    def _moved_and_back(self, before: np.ndarray, moved: np.ndarray, back: np.ndarray):
        """Top-left corner, in screen pixels, of what differs in `moved` only.

        A clock or a sensor reading on screen changes once and stays changed;
        the cursor is what changed and then returned.
        """
        mask = ((np.abs(moved - before) > 40) & (np.abs(moved - back) > 40)
                & (np.abs(before - back) < 20))
        ys, xs = np.nonzero(mask)
        if len(xs) < 6:
            return None
        # Ignore stray pixels far from the bulk of the change.
        near = (np.abs(xs - np.median(xs)) < 80) & (np.abs(ys - np.median(ys)) < 80)
        xs, ys = xs[near], ys[near]
        if len(xs) < 6:
            return None
        kx = before.shape[1] / self.kvm.screen[0]
        ky = before.shape[0] / self.kvm.screen[1]
        # A cursor and its small move fit in a small box. Anything bigger is
        # the screen reacting to the cursor (a button lighting up under it).
        if (xs.max() - xs.min()) / kx > CURSOR_BOX_PIXELS or (ys.max() - ys.min()) / ky > CURSOR_BOX_PIXELS:
            return None
        return xs.min() / kx, ys.min() / ky, xs.max() / kx

    # -- absolute pointer -----------------------------------------------------

    def _absolute(self, buttons: int, x: int, y: int) -> None:
        w, h = self.kvm.screen
        hx = round((x + 0.5) / w * POINTER_MAX)
        hy = round((y + 0.5) / h * POINTER_MAX)
        self.kvm.link.command(f"p {buttons:x} {hx} {hy}")

    def _absolute_works(self, x: int, y: int) -> bool:
        nudge = NUDGE_PIXELS if x + NUDGE_PIXELS < self.kvm.screen[0] else -NUDGE_PIXELS
        self._absolute(0, x, y)
        self.kvm._sync()
        before = self._gray()
        self._absolute(0, x + nudge, y)
        self.kvm._sync()
        moved = self._gray()
        self._absolute(0, x, y)
        self.kvm._sync()
        found = self._moved_and_back(before, moved, self._gray())
        return found is not None and abs(found[0] - x) < 30 and abs(found[1] - y) < 30

    # -- relative mouse -------------------------------------------------------

    def _relative(self, dx: int, dy: int, buttons: int = 0) -> None:
        while dx or dy:
            step_x = max(-127, min(127, dx))
            step_y = max(-127, min(127, dy))
            self.kvm.link.command(f"m {buttons:x} {step_x} {step_y}")
            dx -= step_x
            dy -= step_y

    def _home(self) -> None:
        for _ in range(HOME_REPORTS):
            self.kvm.link.command("m 0 -127 -127")
        self.kvm._sync()
        # Firmware setup adds up the moves it receives within one of its own
        # ticks before keeping the cursor on screen: a move sent right behind
        # this burst is swallowed by it. Measured on an MSI board: lost with no
        # pause, kept from 50 ms.
        time.sleep(HOME_PAUSE_S)

    def _locate(self):
        """Position of the cursor tip in screen pixels, or None if it cannot be seen."""
        # Something else changing on screen at that moment can hide the cursor
        # from the comparison: a second look usually finds it.
        return self._locate_once() or self._locate_once()

    def _locate_once(self):
        before = self._gray()
        self._relative(NUDGE_COUNTS, 0)
        self.kvm._sync()
        moved = self._gray()
        self._relative(-NUDGE_COUNTS, 0)
        self.kvm._sync()
        found = self._moved_and_back(before, moved, self._gray())
        if found is None:
            # Against the right edge the cursor cannot move right: try left.
            self._relative(-NUDGE_COUNTS, 0)
            self.kvm._sync()
            moved = self._gray()
            self._relative(NUDGE_COUNTS, 0)
            self.kvm._sync()
            found = self._moved_and_back(before, moved, self._gray())
            if found is None:
                return None
            return found[0] + NUDGE_COUNTS * self._scale[0], found[1]
        return found[0], found[1]

    def _calibrate_relative(self) -> bool:
        """Measures the pixels per count of the relative mouse. False if no cursor follows it."""
        # From the corner, stop at a few places and note where the cursor is seen.
        stops = ((150, 150), (330, 250), (520, 330), (700, 420))
        seen = []
        at = (0, 0)
        self._home()
        for stop in stops:
            self._relative(stop[0] - at[0], stop[1] - at[1])
            self.kvm._sync()
            at = stop
            found = self._locate()
            if found is not None:
                seen.append((stop, found))
        # Pixels per count: the median over every pair of sightings, so one
        # bad sighting cannot bend the result.
        sx = [(b[1][0] - a[1][0]) / (b[0][0] - a[0][0]) for i, a in enumerate(seen) for b in seen[i + 1:]]
        sy = [(b[1][1] - a[1][1]) / (b[0][1] - a[0][1]) for i, a in enumerate(seen) for b in seen[i + 1:]]
        if not sx:
            return False
        scale = (float(np.median(sx)), float(np.median(sy)))
        if scale[0] < 0.05 or scale[1] < 0.05:
            return False
        self._scale = scale
        self._offset = (float(np.median([f[0] - scale[0] * c[0] for c, f in seen])),
                        float(np.median([f[1] - scale[1] * c[1] for c, f in seen])))
        self._calibrated = True
        return True

    def _relative_goto(self, x: int, y: int) -> None:
        sx, sy = self._scale
        self._home()
        self._relative(round((x - self._offset[0]) / sx), round((y - self._offset[1]) / sy))
        self.kvm._sync()
        # Look where the cursor landed and remove what is left of the error.
        # One pass is enough where counts map to pixels linearly (firmware
        # setup); a system that accelerates the mouse needs a few.
        error = None
        self.corrections = 0
        for _ in range(MAX_CORRECTIONS + 1):
            at = self._locate()
            if at is None:
                break
            ex, ey = x - at[0], y - at[1]
            error = max(abs(ex), abs(ey))
            if error <= ARRIVED_PIXELS:
                break
            self._relative(_at_least_one(ex / sx), _at_least_one(ey / sy))
            self.kvm._sync()
            self.corrections += 1
        self.seen = error is not None
        if error is not None and error > ARRIVED_PIXELS:
            raise PointerError(f"the cursor stopped {error:.0f} pixels away from ({x}, {y}) and could not be "
                               "brought closer; nothing was clicked")

    # -- choosing -------------------------------------------------------------

    def _decide(self, x: int, y: int) -> None:
        if self.forced == "absolute":
            self.mode, self.seen = "absolute", False
        elif self.forced == "relative":
            self.mode = "relative"
            self.seen = self._calibrate_relative()
        elif self._absolute_works(x, y):
            self.mode, self.seen = "absolute", True
        elif self._calibrate_relative():
            self.mode, self.seen = "relative", True
        else:
            # No cursor followed either mouse: a text screen, or a hidden
            # cursor. The absolute pointer is the one operating systems take.
            self.mode, self.seen = "absolute", False

    def forget(self) -> None:
        """Makes the next move find out again which mouse works."""
        self.mode = None
        self._calibrated = False

    def recalibrate(self) -> None:
        """Finds out again, now, which mouse works and how far it moves the cursor.

        The cursor ends where it last was sent, or at the centre of the screen.
        """
        w, h = self.kvm.screen
        x, y = self._pos if self._pos != (0, 0) else (w // 2, h // 2)
        self.forget()
        self.goto(x, y)
        self.kvm._sync()

    def describe(self, after_action: bool = True) -> str:
        """The mouse in use, in words for the caller of a pointer tool. After an
        action, also what that action had to do: corrections, detection."""
        if self.mode is None:
            return "not chosen yet (it is chosen on the first pointer action)"
        if self.mode == "relative":
            text = (f"relative mouse, {self._scale[0]:.2f} x {self._scale[1]:.2f} screenshot pixels per count"
                    if self._calibrated else "relative mouse, not calibrated")
            if after_action and self.corrections:
                text += f", {self.corrections} position correction{'s' if self.corrections > 1 else ''}"
        else:
            text = "absolute pointer"
        if not self.seen:
            text += ", cursor not found on screen so the position is unchecked"
        if after_action and self.just_decided:
            text += " (mouse type just detected)"
        return text

    def _ensure(self, x: int, y: int) -> None:
        # The count of USB configurations changes when the target restarts or
        # hands over from firmware to an operating system: decide again then.
        marker = self.kvm.status()["raw"].get("enum")
        self.just_decided = self.mode is None or marker != self._decided_for
        if self.just_decided:
            self._decided_for = marker
            self._decide(x, y)

    # -- actions --------------------------------------------------------------

    def goto(self, x: int, y: int, held: int = 0) -> None:
        """Moves the cursor to a screen point. `held` is the mask of buttons kept down."""
        if not held:
            self._ensure(x, y)
        if self.mode == "absolute":
            self._absolute(held, x, y)
        elif held:
            # Mid-drag: no trip to the corner, move from where the cursor is.
            self._relative(round((x - self._pos[0]) / self._scale[0]),
                           round((y - self._pos[1]) / self._scale[1]), held)
        else:
            self._relative_goto(x, y)
        self._pos = (x, y)

    def press(self, buttons: int) -> None:
        """Sets which buttons are down, without moving."""
        if self.mode == "relative":
            self.kvm.link.command(f"m {buttons:x} 0 0")
        else:
            self._absolute(buttons, *self._pos)

    def wheel(self, dy: int, dx: int) -> None:
        if self.mode == "relative":
            raise ValueError("this screen only accepts the basic mouse, which has no wheel here: "
                             "use the arrow, pageup and pagedown keys")
        self.kvm.link.command(f"w {dy} {dx}")
