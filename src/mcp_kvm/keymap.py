"""Names and characters to USB HID keyboard usages.

A keyboard sends key positions, not characters: which character appears
depends on the layout selected on the target. LAYOUTS maps each character to
the (modifiers, usage) that produces it under a given target layout.
"""
from __future__ import annotations

MOD_LCTRL, MOD_LSHIFT, MOD_LALT, MOD_LGUI = 0x01, 0x02, 0x04, 0x08
MOD_RCTRL, MOD_RSHIFT, MOD_RALT, MOD_RGUI = 0x10, 0x20, 0x40, 0x80

MODIFIERS = {
    "ctrl": MOD_LCTRL, "control": MOD_LCTRL, "lctrl": MOD_LCTRL, "rctrl": MOD_RCTRL,
    "shift": MOD_LSHIFT, "lshift": MOD_LSHIFT, "rshift": MOD_RSHIFT,
    "alt": MOD_LALT, "lalt": MOD_LALT, "option": MOD_LALT, "ralt": MOD_RALT, "altgr": MOD_RALT,
    "win": MOD_LGUI, "windows": MOD_LGUI, "super": MOD_LGUI, "meta": MOD_LGUI,
    "cmd": MOD_LGUI, "command": MOD_LGUI, "gui": MOD_LGUI, "rwin": MOD_RGUI,
}

# Keys that do not depend on the layout.
NAMED_KEYS = {
    "enter": 0x28, "return": 0x28, "esc": 0x29, "escape": 0x29, "backspace": 0x2A,
    "tab": 0x2B, "space": 0x2C, "capslock": 0x39,
    "printscreen": 0x46, "prtsc": 0x46, "scrolllock": 0x47, "pause": 0x48,
    "insert": 0x49, "ins": 0x49, "home": 0x4A, "pageup": 0x4B, "pgup": 0x4B,
    "delete": 0x4C, "del": 0x4C, "end": 0x4D, "pagedown": 0x4E, "pgdn": 0x4E,
    "right": 0x4F, "left": 0x50, "down": 0x51, "up": 0x52,
    "numlock": 0x53, "menu": 0x65, "apps": 0x65,
    **{f"f{n}": 0x39 + n for n in range(1, 13)},
    **{f"f{n}": 0x5B + n for n in range(13, 25)},
}

KEYPAD_DIGITS = {"1": 0x59, "2": 0x5A, "3": 0x5B, "4": 0x5C, "5": 0x5D,
                 "6": 0x5E, "7": 0x5F, "8": 0x60, "9": 0x61, "0": 0x62}
KEY_NUMLOCK = 0x53


def _us() -> dict[str, tuple[int, int]]:
    table: dict[str, tuple[int, int]] = {"\n": (0, 0x28), "\t": (0, 0x2B), " ": (0, 0x2C)}
    for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
        table[c] = (0, 0x04 + i)
        table[c.upper()] = (MOD_LSHIFT, 0x04 + i)
    for i, (plain, shifted) in enumerate(zip("1234567890", "!@#$%^&*()")):
        table[plain] = (0, 0x1E + i)
        table[shifted] = (MOD_LSHIFT, 0x1E + i)
    for usage, plain, shifted in ((0x2D, "-", "_"), (0x2E, "=", "+"), (0x2F, "[", "{"),
                                  (0x30, "]", "}"), (0x31, "\\", "|"), (0x33, ";", ":"),
                                  (0x34, "'", '"'), (0x35, "`", "~"), (0x36, ",", "<"),
                                  (0x37, ".", ">"), (0x38, "/", "?")):
        table[plain] = (0, usage)
        table[shifted] = (MOD_LSHIFT, usage)
    return table


LAYOUTS = {"us": _us()}


def parse_combo(combo: str, layout: dict[str, tuple[int, int]]) -> tuple[int, list[int]]:
    """Turns "ctrl+shift+esc" into (modifier bits, key usages).

    Parts are modifier names, key names, or single characters. A character is
    looked up in the layout; its own Shift, if any, is added to the modifiers.
    """
    mods = 0
    keys: list[int] = []
    parts = combo.replace(" ", "").split("+")
    if "" in parts:  # "ctrl++" splits into ["ctrl", "", ""]: the empty pair is the plus key
        parts = [p for p in parts if p] + ["+"]
    for part in parts:
        name = part.lower()
        if name in MODIFIERS:
            mods |= MODIFIERS[name]
        elif name in NAMED_KEYS:
            keys.append(NAMED_KEYS[name])
        elif len(part) == 1 and part in layout:
            extra, usage = layout[part]
            mods |= extra
            keys.append(usage)
        elif len(part) == 1 and part.lower() in layout:
            keys.append(layout[part.lower()][1])
        else:
            raise ValueError(f"unknown key {part!r} in {combo!r}")
    if len(keys) > 6:
        raise ValueError(f"more than 6 keys held at once in {combo!r}")
    return mods, keys
