"""Dora node: browser control panel with MJPEG preview and MuJoCo launch buttons."""

import argparse
import json
import os
import queue
import signal
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from typing import Literal

import pyarrow as pa
import uvicorn
from dora import Node
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from camera_probe import probe_cameras
from serial_probe import list_serial_ports
from servo_probe import probe_servo_bus

STATIC_DIR = Path(__file__).parent / "static"
DEMO_DIR = Path(__file__).resolve().parents[2]
OUT_DIR = DEMO_DIR / "out"
MODE_REQUEST_PATH = OUT_DIR / "mode_request.json"
MODE_CURRENT_PATH = OUT_DIR / "mode_current.json"

if str(DEMO_DIR) not in sys.path:
    sys.path.insert(0, str(DEMO_DIR))
import launch_camera  # noqa: E402
import launch_serial  # noqa: E402
import launch_shutdown  # noqa: E402

VALID_MODES = frozenset({"simu", "real", "real-2hands"})
LaunchMode = Literal["simu", "real", "real-2hands"]

# Shared state between HTTP thread and Dora loop
_frame_lock = threading.Lock()
_latest_jpeg: bytes | None = None
_frame_seq = 0
_cmd_queue: queue.Queue[tuple[str, dict]] = queue.Queue()
_cameras: list[dict] = []
_hands_mode = "both"
_port = 8765
_http_ready = threading.Event()
_http_failed: str | None = None
_probe_lock = threading.Lock()


def set_frame(jpeg_bytes: bytes) -> None:
    global _latest_jpeg, _frame_seq
    with _frame_lock:
        _latest_jpeg = jpeg_bytes
        _frame_seq += 1


def get_frame() -> tuple[bytes | None, int]:
    with _frame_lock:
        return _latest_jpeg, _frame_seq


def read_current_mode() -> LaunchMode:
    env_mode = os.environ.get("AH_LAUNCH_MODE")
    if env_mode in VALID_MODES:
        return env_mode  # type: ignore[return-value]
    if MODE_CURRENT_PATH.is_file():
        try:
            data = json.loads(MODE_CURRENT_PATH.read_text(encoding="utf-8"))
            mode = data.get("mode")
            if mode in VALID_MODES:
                return mode  # type: ignore[return-value]
        except (json.JSONDecodeError, OSError):
            pass
    return "simu"


def write_mode_request(mode: LaunchMode) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"mode": mode})
    fd, tmp_path = tempfile.mkstemp(dir=OUT_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, MODE_REQUEST_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_app() -> FastAPI:
    app = FastAPI(title="Hand Tracking")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return html.replace("__HANDS_MODE__", _hands_mode)

    @app.get("/stream")
    def stream() -> StreamingResponse:
        def generate():
            boundary = b"frame"
            last_seq = -1
            while True:
                frame, seq = get_frame()
                if frame is not None and seq != last_seq:
                    last_seq = seq
                    yield (
                        b"--" + boundary + b"\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
                time.sleep(0.03)

        return StreamingResponse(
            generate(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.get("/api/cameras")
    def list_cameras() -> list[dict]:
        return _cameras

    @app.get("/api/camera")
    def get_camera() -> dict:
        return {"index": launch_camera.read_camera_index()}

    class CameraBody(BaseModel):
        index: int

    @app.post("/api/camera")
    def select_camera(body: CameraBody) -> dict:
        index = max(0, int(body.index))
        launch_camera.write_camera_current(index)
        _cmd_queue.put(("set_camera", {"index": index}))
        return {"ok": True, "index": index}

    @app.get("/api/mode")
    def get_mode() -> dict:
        return {"mode": read_current_mode()}

    class ModeBody(BaseModel):
        mode: LaunchMode

    @app.post("/api/mode")
    def set_mode(body: ModeBody) -> dict:
        if body.mode not in VALID_MODES:
            raise HTTPException(status_code=400, detail="Invalid mode")
        current = read_current_mode()
        if body.mode == current:
            return {"ok": True, "restart": False, "mode": current}
        write_mode_request(body.mode)
        return {"ok": True, "restart": True, "mode": body.mode}

    @app.get("/api/serial-ports")
    def list_serial_ports_api() -> list[dict]:
        return list_serial_ports()

    @app.get("/api/serial/probe")
    def probe_serial_port(path: str) -> dict:
        port = path.strip()
        if not port:
            raise HTTPException(status_code=400, detail="Serial port path is required")
        mode = read_current_mode()
        cfg = launch_serial.read_serial_config()
        in_use = mode in ("real", "real-2hands") and (
            port == cfg.get("right") or port == cfg.get("left")
        )
        if in_use:
            return {
                "ok": False,
                "path": port,
                "error": (
                    "Port is in use by the running hardware pipeline. "
                    "Switch to Simulation mode to scan, or change the port to trigger a restart."
                ),
                "blocked": True,
            }
        with _probe_lock:
            return probe_servo_bus(port)

    @app.get("/api/serial")
    def get_serial() -> dict:
        mode = read_current_mode()
        cfg = launch_serial.read_serial_config()
        return {
            "mode": mode,
            "right": cfg.get("right"),
            "left": cfg.get("left"),
            "dual_usb": launch_serial.uses_dual_usb_bus(cfg),
            "hardware_active": mode in ("real", "real-2hands"),
        }

    class SerialBody(BaseModel):
        right: str
        left: str | None = None

    @app.post("/api/serial")
    def set_serial(body: SerialBody) -> dict:
        mode = read_current_mode()
        right = body.right.strip()
        if not right:
            raise HTTPException(status_code=400, detail="Right-hand serial port is required")
        left = body.left.strip() if body.left else None
        if left == right:
            left = None
        requested = {"right": right, "left": left}
        current = launch_serial.read_serial_config()
        if mode not in ("real", "real-2hands"):
            launch_serial.write_serial_current(requested)
            return {
                "ok": True,
                "restart": False,
                "saved": True,
                "message": "Saved for next real-hardware mode.",
            }
        if not launch_serial.serial_config_changed(current, requested):
            return {"ok": True, "restart": False, "right": right, "left": left}
        launch_serial.write_serial_request(requested)
        return {"ok": True, "restart": True, "right": right, "left": left}

    @app.post("/api/launch/right")
    def launch_right() -> dict:
        _cmd_queue.put(("launch_right", {}))
        return {"ok": True}

    @app.post("/api/launch/left")
    def launch_left() -> dict:
        _cmd_queue.put(("launch_left", {}))
        return {"ok": True}

    @app.post("/api/launch/both")
    def launch_both() -> dict:
        _cmd_queue.put(("launch_right", {}))
        _cmd_queue.put(("launch_left", {}))
        return {"ok": True}

    @app.post("/api/shutdown")
    def request_shutdown() -> dict:
        launch_shutdown.write_shutdown_request()
        return {"ok": True, "message": "Shutdown requested"}

    return app


def run_http_server(port: int) -> None:
    global _http_failed
    try:
        app = create_app()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        _http_ready.set()
        server.run()
    except OSError as exc:
        _http_failed = str(exc)
        _http_ready.set()
    except Exception as exc:  # pragma: no cover
        _http_failed = str(exc)
        _http_ready.set()


def drain_commands(node: Node) -> None:
    while True:
        try:
            cmd, payload = _cmd_queue.get_nowait()
        except queue.Empty:
            break
        if cmd == "set_camera":
            node.send_output("set_camera", pa.array([payload["index"]]))
        elif cmd == "launch_right":
            node.send_output("launch_right", pa.array([]))
        elif cmd == "launch_left":
            node.send_output("launch_left", pa.array([]))


def _exit_on_signal(signum, frame) -> None:  # noqa: ARG001
    sys.exit(0)


def main() -> None:
    global _cameras, _hands_mode, _port

    signal.signal(signal.SIGINT, _exit_on_signal)
    signal.signal(signal.SIGTERM, _exit_on_signal)

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--hands",
        choices=["right", "both"],
        default="both",
        help="Show launch buttons for right only or both hands",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AH_WEB_PORT", "8765")),
        help="HTTP server port",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open browser automatically",
    )
    args = parser.parse_args()

    _hands_mode = args.hands
    _port = args.port
    _cameras = probe_cameras()

    http_thread = threading.Thread(
        target=run_http_server,
        args=(args.port,),
        daemon=True,
    )
    http_thread.start()
    if not _http_ready.wait(timeout=5.0):
        raise RuntimeError(f"Web UI HTTP server did not start on port {_port}")
    if _http_failed is not None:
        raise RuntimeError(
            f"Web UI could not bind to port {_port}: {_http_failed}. "
            f"Stop other launch instances or run: lsof -iTCP:{_port} -sTCP:LISTEN"
        )

    if not args.no_browser and not os.environ.get("AH_WEB_NO_BROWSER"):
        time.sleep(0.5)
        webbrowser.open(f"http://127.0.0.1:{args.port}")

    node = Node()
    pa.array([])
    _cmd_queue.put(("set_camera", {"index": launch_camera.read_camera_index()}))

    for event in node:
        if event["type"] == "INPUT":
            event_id = event["id"]
            if event_id == "preview_jpeg":
                value = event["value"]
                if value is not None and len(value) > 0:
                    blob = value[0].as_py()
                    if isinstance(blob, (bytes, bytearray)):
                        set_frame(bytes(blob))
                    elif isinstance(blob, memoryview):
                        set_frame(bytes(blob))
            drain_commands(node)
        elif event["type"] == "ERROR":
            raise RuntimeError(event["error"])

        drain_commands(node)


if __name__ == "__main__":
    main()
