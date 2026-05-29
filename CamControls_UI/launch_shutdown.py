"""Shutdown request from the Web UI to the launch.py supervisor."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent
OUT_DIR = DEMO_DIR / "out"
SHUTDOWN_REQUEST_PATH = OUT_DIR / "shutdown_request.json"


def write_shutdown_request() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"shutdown": True})
    fd, tmp_path = tempfile.mkstemp(dir=OUT_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, SHUTDOWN_REQUEST_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def read_shutdown_request() -> bool:
    if not SHUTDOWN_REQUEST_PATH.is_file():
        return False
    try:
        data = json.loads(SHUTDOWN_REQUEST_PATH.read_text(encoding="utf-8"))
        return bool(data.get("shutdown"))
    except (json.JSONDecodeError, OSError):
        return False


def clear_shutdown_request() -> None:
    try:
        SHUTDOWN_REQUEST_PATH.unlink(missing_ok=True)
    except OSError:
        pass
