"""Probe Feetech SCS0009 servos on a serial port (rustypot ping / model read)."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

DEMO_DIR = Path(__file__).resolve().parents[2]
CONFIG_DIR = DEMO_DIR / "AHControl" / "config"
DEFAULT_BAUDRATE = 1_000_000
DEFAULT_SCAN_MAX_ID = 20


def _motor_ids_from_toml(path: Path) -> list[int]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    motors = data.get("motors") or data.get("Fingers", {}).get("motors", [])
    ids: list[int] = []
    for finger in motors:
        ids.append(int(finger["motor1"]["id"]))
        ids.append(int(finger["motor2"]["id"]))
    return sorted(set(ids))


def expected_id_sets() -> dict[str, list[int]]:
    return {
        "right_hand": _motor_ids_from_toml(CONFIG_DIR / "r_hand.toml"),
        "both_hands_one_bus": _motor_ids_from_toml(CONFIG_DIR / "2hands.toml"),
    }


def port_metadata(path: str) -> dict[str, Any]:
    """USB adapter details from pyserial when available."""
    meta: dict[str, Any] = {"path": path}
    try:
        from serial.tools import list_ports  # type: ignore[import-untyped]

        for info in list_ports.comports():
            if info.device != path:
                continue
            meta["description"] = info.description or None
            meta["manufacturer"] = info.manufacturer or None
            meta["product"] = info.product or None
            meta["usb_serial"] = info.serial_number or None
            if info.vid is not None:
                meta["vid"] = f"0x{info.vid:04x}"
            if info.pid is not None:
                meta["pid"] = f"0x{info.pid:04x}"
            return meta
    except ImportError:
        pass
    meta["description"] = None
    return meta


def _profile_match(found: set[int], expected: list[int]) -> dict[str, Any]:
    exp = set(expected)
    return {
        "expected_ids": sorted(exp),
        "found_ids": sorted(found & exp),
        "missing_ids": sorted(exp - found),
        "unexpected_ids": sorted(found - exp),
        "complete": exp <= found,
    }


def _suggest_layout(found: set[int], profiles: dict[str, list[int]]) -> dict[str, Any]:
    right = set(profiles["right_hand"])
    both = set(profiles["both_hands_one_bus"])
    left_only = both - right

    if not found:
        return {
            "label": "No servos responded",
            "config": None,
            "detail": "Check power, baud rate (1 Mbps), and that this is the hand adapter.",
        }
    if both <= found:
        return {
            "label": "Both hands on one bus",
            "config": "AHControl/config/2hands.toml",
            "detail": f"IDs {sorted(found)} match the 2-hand shared-bus layout.",
        }
    if right <= found and not (found & left_only):
        return {
            "label": "Right hand (8 servos)",
            "config": "AHControl/config/r_hand.toml",
            "detail": f"Found IDs {sorted(found)}.",
        }
    if right <= found and (found & left_only):
        missing_left = sorted(left_only - found)
        return {
            "label": "Partial 2-hand bus",
            "config": "AHControl/config/2hands.toml",
            "detail": (
                f"Right IDs present; missing left-side IDs: {missing_left}"
                if missing_left
                else "Right and some left IDs present."
            ),
        }
    if found <= right:
        missing = sorted(right - found)
        return {
            "label": "Incomplete right hand",
            "config": "AHControl/config/r_hand.toml",
            "detail": f"Missing IDs: {missing}" if missing else "Some right-hand IDs only.",
        }
    return {
        "label": "Unknown ID layout",
        "config": None,
        "detail": f"Found IDs {sorted(found)}; expected 1–8 and/or 11–18 for Pollen hands.",
    }


def probe_servo_bus(
    path: str,
    *,
    baudrate: int = DEFAULT_BAUDRATE,
    scan_max_id: int = DEFAULT_SCAN_MAX_ID,
    timeout: float = 0.2,
) -> dict[str, Any]:
    """Ping servos on a port and compare to AmazingHand TOML configs."""
    from rustypot import Scs0009PyController

    profiles = expected_id_sets()
    result: dict[str, Any] = {
        "path": path,
        "ok": False,
        "error": None,
        "baudrate": baudrate,
        "port": port_metadata(path),
        "motors": [],
        "found_ids": [],
        "profiles": {},
        "suggestion": None,
    }

    try:
        controller = Scs0009PyController(
            serial_port=path,
            baudrate=baudrate,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 — surface serial open errors to UI
        result["error"] = str(exc)
        return result

    motors: list[dict[str, Any]] = []
    found: list[int] = []

    for servo_id in range(1, scan_max_id + 1):
        entry: dict[str, Any] = {"id": servo_id, "responding": False}
        try:
            if not controller.ping(servo_id):
                motors.append(entry)
                continue
            entry["responding"] = True
            found.append(servo_id)
            try:
                entry["model_number"] = int(controller.read_model(servo_id))
            except Exception:
                entry["model_number"] = None
            try:
                entry["present_voltage_v"] = round(
                    float(controller.read_present_voltage(servo_id)), 2
                )
            except Exception:
                entry["present_voltage_v"] = None
            try:
                entry["temperature_c"] = int(controller.read_present_temperature(servo_id))
            except Exception:
                entry["temperature_c"] = None
            try:
                entry["torque_enabled"] = bool(controller.read_torque_enable(servo_id))
            except Exception:
                entry["torque_enabled"] = None
        except Exception as exc:
            entry["error"] = str(exc)
        motors.append(entry)

    found_set = set(found)
    result["motors"] = motors
    result["found_ids"] = sorted(found_set)
    result["profiles"] = {
        name: _profile_match(found_set, ids) for name, ids in profiles.items()
    }
    result["suggestion"] = _suggest_layout(found_set, profiles)
    result["ok"] = True
    return result
