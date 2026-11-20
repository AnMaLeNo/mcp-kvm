"""Helpers shared by the scripts that run on the Windows target (Python 3.7+)."""
import ctypes
import string
import time

import serial
import serial.tools.list_ports

USB_VID = 0x1209
USB_PID = 0x0001


def find_port():
    """Name of the board's control serial port (e.g. "COM5"), or None."""
    for p in serial.tools.list_ports.comports():
        if p.vid == USB_VID and p.pid == USB_PID:
            return p.device
    return None


def wait_port(timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        port = find_port()
        if port:
            return port
        time.sleep(0.2)
    return None


def boot_drive():
    """Root of the RPI-RP2 bootloader drive (e.g. "E:\\"), or None."""
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if mask >> i & 1:
            root = letter + ":\\"
            if k32.GetDriveTypeW(root) != 2:  # DRIVE_REMOVABLE
                continue
            label = ctypes.create_unicode_buffer(261)
            if k32.GetVolumeInformationW(root, label, 261, None, None, None, None, 0) and label.value == "RPI-RP2":
                return root
    return None


def command(ser, text, timeout=3.0):
    """Sends one command line and returns the reply line."""
    ser.reset_input_buffer()
    ser.write((text + "\n").encode("ascii"))
    ser.timeout = timeout
    return ser.readline().decode("ascii", "replace").strip()


def open_port(port):
    ser = serial.Serial(port, 115200, timeout=1, write_timeout=2)
    ser.dtr = True
    return ser
