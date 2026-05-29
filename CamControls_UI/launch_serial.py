"""Serial port selection shared by launch.py and the Web UI."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).resolve().parent
OUT_DIR = DEMO_DIR / "out"
SERIAL_REQUEST_PATH = OUT_DIR / "serial_request.json"
SERIAL_CURRENT_PATH = OUT_DIR / "serial_current.json"

_WEBUI_PKG = DEMO_DIR / "WebUI" / "WebUI"
if str(_WEBUI_PKG) not in sys.path:
    sys.path.insert(0, str(_WEBUI_PKG))

from serial_probe import default_serial_port, list_serial_ports  # noqa: E402


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=OUT_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def normalize_serial_config(data: dict[str, Any] | None) -> dict[str, str | None]:
    """Canonical form: right (primary bus), left (optional second bus)."""
    if not data:
        return {"right": None, "left": None}
    right = data.get("right") or data.get("bus") or data.get("port")
    left = data.get("left")
    if isinstance(right, str):
        right = right.strip() or None
    else:
        right = None
    if isinstance(left, str):
        left = left.strip() or None
    else:
        left = None
    if left and right and left == right:
        left = None
    return {"right": right, "left": left}


def read_serial_config() -> dict[str, str | None]:
    env_right = os.environ.get("AH_SERIAL_PORT")
    if env_right:
        return {"right": env_right, "left": None}
    if SERIAL_CURRENT_PATH.is_file():
        try:
            data = json.loads(SERIAL_CURRENT_PATH.read_text(encoding="utf-8"))
            cfg = normalize_serial_config(data)
            if cfg["right"]:
                return cfg
        except (json.JSONDecodeError, OSError):
            pass
    default = default_serial_port()
    return {"right": default, "left": None}


def write_serial_current(cfg: dict[str, str | None]) -> None:
    _atomic_write_json(SERIAL_CURRENT_PATH, normalize_serial_config(cfg))


def write_serial_request(cfg: dict[str, str | None]) -> None:
    _atomic_write_json(SERIAL_REQUEST_PATH, normalize_serial_config(cfg))


def read_serial_request() -> dict[str, str | None] | None:
    if not SERIAL_REQUEST_PATH.is_file():
        return None
    try:
        data = json.loads(SERIAL_REQUEST_PATH.read_text(encoding="utf-8"))
        return normalize_serial_config(data)
    except (json.JSONDecodeError, OSError):
        return None


def clear_serial_request() -> None:
    try:
        SERIAL_REQUEST_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def serial_config_changed(
    current: dict[str, str | None], requested: dict[str, str | None]
) -> bool:
    return normalize_serial_config(current) != normalize_serial_config(requested)


def resolve_primary_port(cfg: dict[str, str | None], *, fallback: str) -> str:
    return cfg.get("right") or fallback


def uses_dual_usb_bus(cfg: dict[str, str | None]) -> bool:
    left = cfg.get("left")
    right = cfg.get("right")
    return bool(left and right and left != right)
