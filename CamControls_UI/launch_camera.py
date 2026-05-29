"""Camera index selection shared by launch.py and the Web UI."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).resolve().parent
OUT_DIR = DEMO_DIR / "out"
CAMERA_CURRENT_PATH = OUT_DIR / "camera_current.json"
DEFAULT_CAMERA_INDEX = 0


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


def read_camera_index() -> int:
    env_index = os.environ.get("AH_CAMERA_INDEX")
    if env_index is not None:
        try:
            return max(0, int(env_index))
        except ValueError:
            pass
    if CAMERA_CURRENT_PATH.is_file():
        try:
            data = json.loads(CAMERA_CURRENT_PATH.read_text(encoding="utf-8"))
            return max(0, int(data.get("index", DEFAULT_CAMERA_INDEX)))
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return DEFAULT_CAMERA_INDEX


def write_camera_current(index: int) -> None:
    _atomic_write_json(CAMERA_CURRENT_PATH, {"index": max(0, int(index))})
