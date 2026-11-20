"""Copies a UF2 onto the board from the Windows target it is plugged into.

    python flash.py <firmware.uf2>

If the board is not in its bootloader yet and exposes a control serial port
here, asks it to enter the bootloader (the "bootsel" command, then a 1200 baud
open if the firmware does not answer). Prints "FLASH_COPIED" on success and
exits non-zero otherwise. Checking that the new firmware answers is left to
the caller, which knows how to reach it.
"""
import shutil
import sys
import time

import serial

import kvm_port


def wait_drive(timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        drive = kvm_port.boot_drive()
        if drive:
            return drive
        time.sleep(0.3)
    return None


def enter_bootloader(port):
    try:
        with kvm_port.open_port(port) as ser:
            print("bootsel command on", port, "->", kvm_port.command(ser, "bootsel", 1.0))
    except (serial.SerialException, OSError) as e:
        print("bootsel command failed:", e)
    if wait_drive(5):
        return
    print("still running: 1200 baud touch on", port)
    try:
        serial.Serial(port, 1200).close()
    except (serial.SerialException, OSError) as e:
        print("1200 baud touch failed:", e)


def main():
    uf2 = sys.argv[1]
    drive = wait_drive(4)
    if not drive:
        port = kvm_port.find_port()
        if not port:
            sys.exit("FLASH_FAIL no RPI-RP2 drive and no control port here: "
                     "hold BOOT while plugging the board")
        enter_bootloader(port)
        drive = wait_drive(15)
    if not drive:
        sys.exit("FLASH_FAIL the RPI-RP2 drive did not appear")

    print("copying to", drive)
    try:
        shutil.copyfile(uf2, drive + "firmware.uf2")
    except OSError as e:
        # The board reboots as soon as the last block lands; Windows may report
        # the vanished drive as an error although the copy went through.
        print("copy ended with:", e)
    print("FLASH_COPIED")


if __name__ == "__main__":
    main()
