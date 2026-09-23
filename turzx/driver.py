"""Output sinks: the Turzx 5" serial display (rev C protocol) and a PNG preview.

Protocol reference: turing-smart-screen-python, library/lcd/lcd_comm_rev_c.py.
The 5" panel's native framebuffer is 800x480 landscape, row-major.
"""

from __future__ import annotations

import logging
import string
import time
from math import ceil
from pathlib import Path

import numpy as np
import serial
from PIL import Image
from serial.tools.list_ports import comports

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 800, 480
PACKET = 250  # every message is padded to a multiple of this

HELLO = bytes((0x01, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0xC5, 0xD3))
OPTIONS = bytes((0x7D, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x05, 0x00, 0x00, 0x00, 0x2D))
SET_BRIGHTNESS = bytes((0x7B, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00))
TURN_OFF = bytes((0x83, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
STOP_VIDEO = bytes((0x79, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
STOP_MEDIA = bytes((0x96, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
QUERY_STATUS = bytes((0xCF, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
PRE_UPDATE_BITMAP = bytes((0x86, 0xEF, 0x69, 0x00, 0x00, 0x00, 0x01))
START_DISPLAY_BITMAP = bytes((0x2C,))
DISPLAY_BITMAP_5INCH = bytes((0xC8, 0xEF, 0x69, 0x00, 0x17, 0x70))
UPDATE_BITMAP = bytes((0xCC, 0xEF, 0x69, 0x00))

AWAKE_SERIAL = "20080411"
AWAKE_IDS = {(0x1D6B, 0x0106), (0x1D6B, 0x0121), (0x0525, 0xA4A7)}
SLEEP_IDS = {(0x1A86, 0xCA21)}


def _chunk_join(data: bytes) -> bytes:
    """Insert a 0x00 after every 249 bytes, as the firmware expects."""
    return b"\x00".join(data[i:i + 249] for i in range(0, len(data), 249))


def _pixels(image: Image.Image, bgra: bool) -> tuple[bytes, int]:
    rgba = np.asarray(image.convert("RGBA"))
    if bgra:
        return np.take(rgba, (2, 1, 0, 3), axis=-1).tobytes(), 4
    return np.take(rgba, (2, 1, 0), axis=-1).tobytes(), 3


def find_port() -> str | None:
    ports = list(comports())
    for p in ports:
        if p.serial_number == AWAKE_SERIAL or (p.vid, p.pid) in AWAKE_IDS:
            return p.device
    return None


def wake(port: str, retries: int = 15) -> str | None:
    """Opening a sleeping device's port makes it re-enumerate as the awake device."""
    for _ in range(retries):
        try:
            serial.Serial(port, 115200, timeout=1, rtscts=True).close()
        except serial.SerialException:
            pass
        if (awake := find_port()) is not None:
            time.sleep(1)
            return awake
        time.sleep(1)
    return None


class TurzxDisplay:
    size = (WIDTH, HEIGHT)

    def __init__(self, port: str = "auto", brightness: int = 50, flip: bool = False):
        if port == "auto":
            port = find_port()
            if port is None:
                raise OSError("Turzx display not found (is it plugged in and awake?)")
        self.flip = flip
        self._count = 0
        # Set when the screen drops a partial update and asks for a resend; the
        # caller must then push a full frame to get it rendering again.
        self.needs_full = False
        self.serial = serial.Serial(port, 115200, timeout=1, rtscts=True, write_timeout=10)
        log.info("opened %s", port)
        try:
            self._hello()
            self._send(STOP_VIDEO)
            self._send(STOP_MEDIA, read=1024)
            self.set_brightness(brightness)
            # start mode default, no hardware flip (we rotate in software), sleep interval off
            self._send(OPTIONS, bytes((0x00, 0x00, 0x00, 0x00)))
        except Exception:
            self.serial.close()
            raise

    def _send(self, cmd: bytes, payload: bytes = b"", pad: int = 0x00, read: int | None = None) -> bytes:
        msg = cmd + payload
        if len(msg) % PACKET:
            msg += bytes((pad,)) * (PACKET * ceil(len(msg) / PACKET) - len(msg))
        self.serial.write(msg)
        return self.serial.read(read) if read else b""

    def _hello(self) -> None:
        self.serial.reset_input_buffer()
        for _ in range(10):
            raw = self._send(HELLO, read=23)
            ident = "".join(c for c in raw.decode(errors="ignore") if c in string.printable)
            self.serial.reset_input_buffer()
            if ident.startswith("chs_"):
                break
            log.warning("unexpected hello response %r, retrying", ident)
            time.sleep(1)
        else:
            raise OSError("display did not answer HELLO")
        try:
            self.rom = int(ident.split(".")[2])
        except (IndexError, ValueError):
            self.rom = 87
        if not 80 <= self.rom <= 100:
            self.rom = 87
        log.info("display id %r, rom %d", ident, self.rom)

    def set_brightness(self, percent: int) -> None:
        percent = max(0, min(100, percent))
        self._send(SET_BRIGHTNESS, bytes((int(percent / 100 * 255),)))

    def show_full(self, image: Image.Image) -> None:
        assert image.size == self.size
        if self.flip:
            image = image.rotate(180)
        data, _ = _pixels(image, bgra=True)
        self._send(PRE_UPDATE_BITMAP)
        self._send(START_DISPLAY_BITMAP, pad=0x2C)
        self._send(DISPLAY_BITMAP_5INCH, (480 * 480 // 64).to_bytes(2, "big"))
        self._send(b"", _chunk_join(data), read=1024)
        self._send(QUERY_STATUS, read=1024)
        self._count = 0
        self.needs_full = False

    def show_region(self, image: Image.Image, x: int, y: int) -> None:
        w, h = image.size
        if self.flip:
            image = image.rotate(180)
            x, y = WIDTH - x - w, HEIGHT - y - h
        data, px = _pixels(image, bgra=self.rom > 88)
        stride = w * px
        body = bytearray()
        for row in range(h):
            body += ((y + row) * WIDTH + x).to_bytes(3, "big")
            body += w.to_bytes(2, "big")
            body += data[row * stride:(row + 1) * stride]
        header = UPDATE_BITMAP + (len(body) + 2).to_bytes(3, "big") + b"\x00" * 3 + self._count.to_bytes(4, "big")
        if len(body) > PACKET:
            body = bytearray(_chunk_join(bytes(body)))
        body += b"\xef\x69"
        self._send(b"", header)
        self._send(b"", bytes(body))
        status = self._send(QUERY_STATUS, read=1024).rstrip(b"\x00").decode(errors="replace")
        log.debug("region %dx%d@%d,%d count=%d status=%r", w, h, x, y, self._count, status)
        self._count += 1
        if not status.startswith("needReSend:0"):
            log.warning("screen dropped an update (status %r); will resend full frame", status)
            self.needs_full = True

    def close(self) -> None:
        self.serial.close()


class PreviewDisplay:
    """Renders to a PNG instead of hardware, for designing layouts."""

    size = (WIDTH, HEIGHT)

    def __init__(self, path: str | Path = "preview.png"):
        self.path = Path(path)
        self.frame = Image.new("RGB", self.size)

    def show_full(self, image: Image.Image) -> None:
        self.frame = image.copy()
        self.frame.save(self.path)

    def show_region(self, image: Image.Image, x: int, y: int) -> None:
        self.frame.paste(image, (x, y))
        self.frame.save(self.path)

    def set_brightness(self, percent: int) -> None:
        pass

    def close(self) -> None:
        pass
