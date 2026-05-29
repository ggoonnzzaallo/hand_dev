"""List serial ports likely used by AmazingHand (Feetech USB adapters)."""

from __future__ import annotations

import glob
import os
import sys


def _glob_ports(patterns: list[str]) -> list[str]:
    seen: set[str] = set()
    ports: list[str] = []
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)):
            if path in seen:
                continue
            seen.add(path)
            ports.append(path)
    return ports


def list_serial_ports() -> list[dict]:
    """Return available ports as {path, label} dicts (newest paths last)."""
    if sys.platform == "darwin":
        patterns = [
            "/dev/cu.usbmodem*",
            "/dev/cu.usbserial*",
            "/dev/cu.wchusbserial*",
        ]
    elif sys.platform.startswith("linux"):
        patterns = ["/dev/ttyACM*", "/dev/ttyUSB*"]
    elif sys.platform == "win32":
        patterns = []  # handled below
    else:
        patterns = []

    paths = _glob_ports(patterns)

    if sys.platform == "win32":
        try:
            from serial.tools import list_ports  # type: ignore[import-untyped]

            for info in sorted(list_ports.comports()):
                paths.append(info.device)
        except ImportError:
            pass

    entries: list[dict] = []
    for path in paths:
        label = f"{os.path.basename(path)} — {path}"
        meta: dict = {"path": path, "label": label}
        try:
            from serial.tools import list_ports  # type: ignore[import-untyped]

            for info in list_ports.comports():
                if info.device != path:
                    continue
                if info.description:
                    label = f"{os.path.basename(path)} — {info.description}"
                if info.manufacturer:
                    meta["manufacturer"] = info.manufacturer
                if info.product:
                    meta["product"] = info.product
                if info.serial_number:
                    meta["usb_serial"] = info.serial_number
                meta["label"] = label
                break
        except ImportError:
            pass
        entries.append(meta)
    return entries


def default_serial_port() -> str | None:
    ports = list_serial_ports()
    if ports:
        return ports[0]["path"]
    return None
