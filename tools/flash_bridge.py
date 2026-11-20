"""Flashes the bridge board, which is plugged into this machine (macOS).

    .venv/bin/python tools/flash_bridge.py [firmware/build/kvm_bridge.uf2]

Asks the running bridge firmware to enter its bootloader ("!bootsel", then a
1200 baud open), copies the UF2 to the RPI-RP2 volume and checks that the new
firmware answers. A board running something else must be put in its bootloader
first (BOOT held while plugging, or its own means).
"""
import shutil
import sys
import time
from pathlib import Path

import serial
from serial.tools import list_ports

VOLUME = Path("/Volumes/RPI-RP2")
USB_ID = (0x1209, 0x0002)


def find_port():
    for p in list_ports.comports():
        if (p.vid, p.pid) == USB_ID:
            return p.device
    return None


def wait_for(probe, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        found = probe()
        if found:
            return found
        time.sleep(0.2)
    return None


def command(port, text, timeout=2.0):
    with serial.Serial(port, 115200, timeout=timeout, write_timeout=2) as ser:
        ser.reset_input_buffer()
        ser.write((text + "\n").encode())
        return ser.readline().decode("ascii", "replace").strip()


def main():
    uf2 = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent.parent / "firmware/build/kvm_bridge.uf2")
    if not VOLUME.is_dir():
        port = find_port()
        if not port:
            sys.exit("FLASH_FAIL no RPI-RP2 volume and no bridge serial port: put the board in its bootloader")
        try:
            print("!bootsel on", port, "->", command(port, "!bootsel", 1.0))
        except (serial.SerialException, OSError) as e:
            print("!bootsel failed:", e)
        if not wait_for(VOLUME.is_dir, 6) and find_port():
            print("still running: 1200 baud touch")
            try:
                serial.Serial(port, 1200).close()
            except (serial.SerialException, OSError) as e:
                print("1200 baud touch failed:", e)
    if not wait_for(VOLUME.is_dir, 15):
        sys.exit("FLASH_FAIL the RPI-RP2 volume did not appear")
    time.sleep(0.5)
    try:
        shutil.copyfile(uf2, VOLUME / "firmware.uf2")
    except OSError as e:
        # The board reboots as soon as the last block lands.
        print("copy ended with:", e)
    port = wait_for(find_port, 20)
    if not port:
        sys.exit("FLASH_FAIL the bridge serial port did not appear after the copy")
    last = None
    for _ in range(10):
        try:
            answer = command(port, "!info")
            if answer.startswith("ok"):
                print("FLASH_OK", port, answer)
                return
            last = repr(answer)
        except (serial.SerialException, OSError) as e:
            last = str(e)
        time.sleep(0.5)
    sys.exit(f"FLASH_FAIL the bridge does not answer on {port}: {last}")


if __name__ == "__main__":
    main()
