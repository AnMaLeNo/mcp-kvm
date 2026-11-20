"""Relays text lines between stdin/stdout and the board's control serial port.

    ssh user@target <python> -u serial_bridge.py

Development transport: it lets the controlling machine reach a board whose
only USB link goes to the target. Lines read on stdin go to the board; lines
from the board go to stdout. Lines starting with "#" are status messages from
the bridge itself ("# connected COM5", "# disconnected").
"""
import sys
import threading
import time

import serial

import kvm_port

lock = threading.Lock()
current = [None]  # the open serial port, or None while the board is away


def say(text):
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def reader():
    while True:
        port = kvm_port.find_port()
        if not port:
            time.sleep(0.3)
            continue
        try:
            ser = kvm_port.open_port(port)
        except (serial.SerialException, OSError):
            time.sleep(0.3)
            continue
        ser.timeout = 0.2
        with lock:
            current[0] = ser
        say("# connected " + port)
        try:
            while True:
                data = ser.readline()
                if data:
                    say(data.decode("ascii", "replace").rstrip("\r\n"))
        except (serial.SerialException, OSError):
            pass
        with lock:
            current[0] = None
        try:
            ser.close()
        except (serial.SerialException, OSError):
            pass
        say("# disconnected")


def main():
    threading.Thread(target=reader, daemon=True).start()
    for raw in sys.stdin:
        text = raw.strip()
        if not text:
            continue
        with lock:
            ser = current[0]
            if ser is None:
                say("err not connected")
                continue
            try:
                ser.write((text + "\n").encode("ascii", "replace"))
            except (serial.SerialException, OSError) as e:
                say("err write failed: %s" % e)


if __name__ == "__main__":
    main()
