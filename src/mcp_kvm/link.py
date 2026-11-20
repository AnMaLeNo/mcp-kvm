"""Command channel to the board: one text line out, one reply line back.

Two transports share the same interface:

- SerialLink: a serial port on this machine. Normally the bridge board,
  which relays to the target board over a wire.
- SshBridgeLink: the target board's own serial port, on the target, reached
  through tools/win/serial_bridge.py over SSH (single-board setup, needs a
  running system and an SSH server on the target).
"""
from __future__ import annotations

import queue
import subprocess
import threading
import time

USB_VID = 0x1209
USB_PID_BRIDGE = 0x0002   # bridge board
USB_PID_TARGET = 0x0001   # target board with its own serial port


class LinkError(RuntimeError):
    pass


class Link:
    def command(self, text: str, timeout: float = 3.0) -> str:
        """Sends a command; returns what follows "ok" in the reply. Raises LinkError."""
        reply = self._exchange(text, timeout)
        if reply == "ok" or reply.startswith("ok "):
            return reply[3:]
        raise LinkError(f"board refused {text.split(' ', 1)[0]!r}: {reply}")

    def _exchange(self, text: str, timeout: float) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass


def find_local_port() -> str | None:
    from serial.tools import list_ports
    ports = {p.pid: p.device for p in list_ports.comports() if p.vid == USB_VID}
    return ports.get(USB_PID_BRIDGE) or ports.get(USB_PID_TARGET)


class SerialLink(Link):
    def __init__(self, port: str | None = None):
        self._port_name = port
        self._ser = None
        self._lock = threading.Lock()

    def _exchange(self, text: str, timeout: float) -> str:
        import serial
        with self._lock:
            try:
                if self._ser is None:
                    name = self._port_name or find_local_port()
                    if not name:
                        raise LinkError("board not found: no serial port with the mcp-kvm USB identity")
                    self._ser = serial.Serial(name, 115200, timeout=timeout, write_timeout=2)
                self._ser.reset_input_buffer()
                self._ser.timeout = timeout
                self._ser.write((text + "\n").encode("ascii"))
                reply = self._ser.readline().decode("ascii", "replace").strip()
            except (serial.SerialException, OSError) as e:
                self.close()
                raise LinkError(f"serial link to the board failed: {e}") from e
            if not reply:
                raise LinkError(f"board did not answer {text.split(' ', 1)[0]!r} within {timeout:.0f} s")
            return reply

    def close(self) -> None:
        if self._ser is not None:
            try:
                self._ser.close()
            except OSError:
                pass
            self._ser = None


# The Windows target's default SSH shell is PowerShell.
_BRIDGE_COMMAND = ('& "$env:USERPROFILE\\mcp-kvm-dev\\venv\\Scripts\\python.exe" -u '
                   '"$env:USERPROFILE\\mcp-kvm-dev\\serial_bridge.py"')


class SshBridgeLink(Link):
    def __init__(self, ssh_target: str):
        self.ssh_target = ssh_target
        self._proc: subprocess.Popen | None = None
        self._lines: queue.Queue[str] = queue.Queue()
        self._connected = threading.Event()
        self._lock = threading.Lock()

    def _reader(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            text = raw.strip()
            if text.startswith("# connected"):
                self._connected.set()
            elif text.startswith("# disconnected"):
                self._connected.clear()
            elif text:
                self._lines.put(text)
        self._connected.clear()

    def _ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._connected.clear()
        self._proc = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
             self.ssh_target, _BRIDGE_COMMAND],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1)
        threading.Thread(target=self._reader, args=(self._proc,), name="kvm-bridge", daemon=True).start()

    def _exchange(self, text: str, timeout: float) -> str:
        with self._lock:
            self._ensure()
            if not self._connected.wait(15):
                if self._proc.poll() is not None:
                    raise LinkError(f"SSH bridge to {self.ssh_target} exited (code {self._proc.returncode})")
                raise LinkError(f"board not found on {self.ssh_target}: its control serial port is absent")
            while not self._lines.empty():
                self._lines.get_nowait()
            try:
                self._proc.stdin.write(text + "\n")
                self._proc.stdin.flush()
            except OSError as e:
                raise LinkError(f"SSH bridge to {self.ssh_target} is gone: {e}") from e
            deadline = time.monotonic() + timeout
            try:
                return self._lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise LinkError(f"board did not answer {text.split(' ', 1)[0]!r} within {timeout:.0f} s") from None

    def close(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc = None


def open_link(spec: str | None, ssh_fallback: str | None = None) -> Link:
    """Builds a link from KVM_LINK: "serial", "serial:<port>" or "ssh:<user@host>".

    Empty means automatic: the local serial port if a board is plugged in
    here, otherwise SSH to `ssh_fallback` when given.
    """
    if spec and spec.startswith("ssh:"):
        return SshBridgeLink(spec[4:])
    if spec and spec.startswith("serial:"):
        return SerialLink(spec[7:])
    if not spec and ssh_fallback and not find_local_port():
        return SshBridgeLink(ssh_fallback)
    return SerialLink()
