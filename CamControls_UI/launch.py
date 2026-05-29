#!/usr/bin/env python3
"""One-command launcher for CamControls UI (hand tracking demos)."""

import argparse
import atexit
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import launch_camera
import launch_serial
import launch_shutdown

DEMO_DIR = Path(__file__).resolve().parent
OUT_DIR = DEMO_DIR / "out"
MODE_REQUEST_PATH = OUT_DIR / "mode_request.json"
MODE_CURRENT_PATH = OUT_DIR / "mode_current.json"
BUILD_STAMP = OUT_DIR / ".dora_build_ok"

MODES = {
    "simu": "dataflow_tracking_simu.yml",
    "real": "dataflow_tracking_real.yml",
    "real-2hands": "dataflow_tracking_real_2hands.yml",
}

DEFAULT_MODE = "simu"
DEFAULT_PORT = 8765
REAL_MODES = frozenset({"real", "real-2hands"})
AHCONTROL_BIN = DEMO_DIR / "target" / "debug" / "AHControl"
DEFAULT_SERIAL = "/dev/ttyACM0"

_HAND_CONTROLLER_BLOCK = re.compile(
    r"  - id: hand_controller\n(?:    .*\n)+",
    re.MULTILINE,
)


def _dora_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Ensure cargo/rust are visible to dora build (conda shells often omit ~/.cargo/bin)."""
    env = (base or os.environ).copy()
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    cargo_bin_dir = str(cargo_home / "bin")
    path = env.get("PATH", "")
    if cargo_bin_dir not in path.split(os.pathsep):
        env["PATH"] = f"{cargo_bin_dir}{os.pathsep}{path}" if path else cargo_bin_dir
    # Dora spawns several nodes at once; each `uv run` syncs by default and can race,
    # leaving mediapipe (and other wheels) half-installed → import errors.
    env["UV_NO_SYNC"] = "1"
    return env


def run(
    cmd: list[str],
    *,
    check: bool = True,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=cwd or DEMO_DIR,
        check=check,
        env=env,
    )


def dora_daemon_ok() -> bool:
    try:
        result = subprocess.run(
            ["dora", "check"],
            cwd=DEMO_DIR,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode == 0 and "Coordinator: ok" in (result.stdout + result.stderr)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def ensure_dora_up() -> None:
    if dora_daemon_ok():
        print("Dora daemon is running.")
        return
    print("Starting Dora daemon…")
    subprocess.Popen(
        ["dora", "up"],
        cwd=DEMO_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(60):
        time.sleep(0.5)
        if dora_daemon_ok():
            print("Dora daemon started.")
            return
    print(
        "Could not start Dora daemon. Try: dora destroy && dora up",
        file=sys.stderr,
    )
    sys.exit(1)


def wait_for_web_ui(port: int, timeout_s: float = 120.0) -> bool:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.5)
    return False


def write_mode_current(mode: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    MODE_CURRENT_PATH.write_text(json.dumps({"mode": mode}), encoding="utf-8")


def read_mode_request() -> str | None:
    if not MODE_REQUEST_PATH.is_file():
        return None
    try:
        data = json.loads(MODE_REQUEST_PATH.read_text(encoding="utf-8"))
        mode = data.get("mode")
        if mode in MODES:
            return mode
    except (json.JSONDecodeError, OSError):
        pass
    return None


def clear_mode_request() -> None:
    try:
        MODE_REQUEST_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def pids_listening_on_port(port: int) -> list[int]:
    """PIDs with a TCP listener on localhost:port (macOS/Linux)."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0 or not result.stdout.strip():
        return []
    pids: list[int] = []
    for token in result.stdout.strip().split():
        if token.isdigit():
            pids.append(int(token))
    return pids


def _pgrep_pids(pattern: str) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        if line.strip().isdigit():
            pids.append(int(line.strip()))
    return pids


def _signal_pids(pids: list[int], sig: signal.Signals, *, exclude: set[int]) -> None:
    for pid in pids:
        if pid in exclude:
            continue
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            pass


def cleanup_orphaned_demo_processes(*, verbose: bool = True, exclude_pids: set[int] | None = None) -> None:
    """Stop Dora node processes that often linger after Ctrl+C or mode switches."""
    exclude = exclude_pids or set()
    exclude.add(os.getpid())

    demo = str(DEMO_DIR)
    patterns = [
        f"{demo}/WebUI/WebUI/main.py",
        f"{demo}/HandTracking/HandTracking/main.py",
        f"{demo}/AHSimulation/AHSimulation/mj_mink_right.py",
        f"{demo}/AHSimulation/AHSimulation/mj_mink_left.py",
        f"{demo}/target/debug/AHControl",
        "mj_mink_right.py",
        "mj_mink_left.py",
    ]
    venv_mj = DEMO_DIR / ".venv" / "bin" / "mjpython"
    if venv_mj.is_file():
        patterns.append(str(venv_mj))

    seen: set[int] = set()
    for pattern in patterns:
        for pid in _pgrep_pids(pattern):
            if pid not in exclude:
                seen.add(pid)

    if not seen:
        return

    if verbose:
        print(f"Stopping orphaned demo processes (PIDs: {', '.join(map(str, sorted(seen)))})…")

    _signal_pids(sorted(seen), signal.SIGTERM, exclude=exclude)
    time.sleep(0.4)
    survivors = [pid for pid in seen if _process_alive(pid)]
    if survivors:
        _signal_pids(survivors, signal.SIGKILL, exclude=exclude)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _terminate_process_tree(proc: subprocess.Popen, *, grace_s: float = 12.0) -> None:
    """Send SIGINT → SIGTERM → SIGKILL to the dora run process group."""
    if proc.poll() is not None:
        return

    def _signal_group(sig: signal.Signals) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except ProcessLookupError:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass

    _signal_group(signal.SIGINT)
    try:
        proc.wait(timeout=grace_s)
        return
    except subprocess.TimeoutExpired:
        pass

    _signal_group(signal.SIGTERM)
    try:
        proc.wait(timeout=3)
        return
    except subprocess.TimeoutExpired:
        pass

    _signal_group(signal.SIGKILL)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def free_web_ui_port(port: int) -> None:
    """Kill orphaned WebUI HTTP servers left after a Dora mode restart."""
    cleanup_orphaned_demo_processes(verbose=False)
    pids = pids_listening_on_port(port)
    if not pids:
        return
    print(f"Stopping stale web UI on port {port} (PIDs: {', '.join(map(str, pids))})…")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(30):
        if not pids_listening_on_port(port):
            return
        time.sleep(0.1)
    for pid in pids_listening_on_port(port):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def detect_serial_port() -> str:
    """Pick the hand serial device (env, saved UI choice, or first detected port)."""
    cfg = launch_serial.read_serial_config()
    port = launch_serial.resolve_primary_port(cfg, fallback=DEFAULT_SERIAL)
    if port != DEFAULT_SERIAL:
        return port
    if sys.platform == "darwin":
        ports = sorted(glob.glob("/dev/cu.usbmodem*"))
        if ports:
            return ports[0]
    if sys.platform.startswith("linux"):
        for pattern in ("/dev/ttyACM*", "/dev/ttyUSB*"):
            ports = sorted(glob.glob(pattern))
            if ports:
                return ports[0]
    return DEFAULT_SERIAL


def _patch_dataflow_camera(content: str, camera_index: int) -> str:
    return re.sub(r"--camera\s+\d+", f"--camera {camera_index}", content)


def _patch_dataflow_serial(
    content: str,
    *,
    mode: str,
    serial_cfg: dict[str, str | None],
    build_cmd: str,
) -> str:
    right = launch_serial.resolve_primary_port(serial_cfg, fallback=DEFAULT_SERIAL)
    left = serial_cfg.get("left")

    if mode == "real-2hands" and launch_serial.uses_dual_usb_bus(serial_cfg):
        block = f"""  - id: hand_controller_right
    build: {build_cmd}
    path: target/debug/AHControl
    args: --serialport {right} --config AHControl/config/r_hand.toml
    inputs:
      mj_r_joints_pos: r_hand_simulation/mj_r_joints_pos

  - id: hand_controller_left
    build: {build_cmd}
    path: target/debug/AHControl
    args: --serialport {left} --config AHControl/config/r_hand.toml
    inputs:
      mj_l_joints_pos: l_hand_simulation/mj_l_joints_pos
"""
        if not _HAND_CONTROLLER_BLOCK.search(content):
            raise ValueError("Could not patch dual hand_controller block in dataflow")
        return _HAND_CONTROLLER_BLOCK.sub(block, content, count=1)

    return re.sub(r"--serialport\s+\S+", f"--serialport {right}", content)


def _cargo_bin() -> str:
    """Resolve cargo binary (conda/base shells often omit ~/.cargo/bin from PATH)."""
    cargo_home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    candidate = cargo_home / "bin" / "cargo"
    if candidate.is_file():
        return str(candidate)
    found = shutil.which("cargo")
    if found:
        return found
    print(
        "Rust/Cargo is required for real hardware mode.\n"
        "  Install: curl -sSf https://sh.rustup.rs | sh\n"
        "  Then run: source \"$HOME/.cargo/env\" && cd Demo && cargo build -p AHControl\n"
        "  Or restart your terminal after installing rustup.",
        file=sys.stderr,
    )
    sys.exit(1)


def _ahcontrol_build_command() -> str:
    """Dora runs build from the dataflow file's directory (out/), so use absolute paths."""
    manifest = DEMO_DIR / "Cargo.toml"
    return f"{_cargo_bin()} build --manifest-path {manifest} -p AHControl"


def ensure_ahcontrol_built() -> None:
    if AHCONTROL_BIN.is_file():
        return
    print("Building AHControl (Rust motor controller)…")
    run(
        [_cargo_bin(), "build", "--manifest-path", str(DEMO_DIR / "Cargo.toml"), "-p", "AHControl"],
        cwd=DEMO_DIR,
        env=_dora_env(),
    )


def prepare_dataflow(mode: str) -> str:
    """Return dataflow path (relative to CamControls_UI/) for dora build/run."""
    base_name = MODES[mode]
    camera_index = launch_camera.read_camera_index()
    src = (DEMO_DIR / base_name).read_text(encoding="utf-8")
    patched = _patch_dataflow_camera(src, camera_index)
    print(f"Camera index: {camera_index}")

    if mode in REAL_MODES:
        ensure_ahcontrol_built()
        serial_cfg = launch_serial.read_serial_config()
        port = launch_serial.resolve_primary_port(serial_cfg, fallback=DEFAULT_SERIAL)
        if port == DEFAULT_SERIAL:
            print(
                f"Warning: no USB serial device found; using {DEFAULT_SERIAL}. "
                "Pick a port in the web UI or set AH_SERIAL_PORT.",
                file=sys.stderr,
            )
        elif launch_serial.uses_dual_usb_bus(serial_cfg):
            print(f"Serial: right={serial_cfg['right']} left={serial_cfg['left']} (dual USB)")
        else:
            print(f"Serial port: {port}")

        build_cmd = _ahcontrol_build_command()
        patched = patched.replace("build: cargo build -p AHControl", f"build: {build_cmd}")
        patched = _patch_dataflow_serial(
            patched, mode=mode, serial_cfg=serial_cfg, build_cmd=build_cmd
        )
        launch_serial.write_serial_current(serial_cfg)

    # Must live in CamControls_UI/ (not out/) so dora resolves HandTracking/, WebUI/, etc. correctly.
    runtime_name = base_name.replace(".yml", "_runtime.yml")
    runtime_path = DEMO_DIR / runtime_name
    runtime_path.write_text(patched, encoding="utf-8")
    return runtime_name


def should_build(force_build: bool, mode: str, dataflow: str) -> bool:
    if force_build:
        return True
    if mode in REAL_MODES and not AHCONTROL_BIN.is_file():
        return True
    runtime_path = DEMO_DIR / dataflow
    if runtime_path.is_file() and BUILD_STAMP.is_file():
        if runtime_path.stat().st_mtime > BUILD_STAMP.stat().st_mtime:
            return True
    if not BUILD_STAMP.is_file():
        return True
    for yml in MODES.values():
        yml_path = DEMO_DIR / yml
        if yml_path.is_file() and yml_path.stat().st_mtime > BUILD_STAMP.stat().st_mtime:
            return True
    return False


def build_dataflow(dataflow: str) -> None:
    print("Building dataflow…")
    run(["dora", "build", dataflow, "--uv"], env=_dora_env())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_STAMP.touch()


def ensure_demo_dependencies() -> None:
    """Install/sync the uv workspace once before dora spawns parallel `uv run` nodes."""
    checks = (
        ("mujoco", "MuJoCo (AHSimulation)"),
        ("cv2", "OpenCV (HandTracking / WebUI)"),
        ("pyarrow", "PyArrow (WebUI)"),
    )
    missing = [label for module, label in checks if _import_missing(module)]
    mediapipe_ok = _mediapipe_solutions_ok()

    if missing or not mediapipe_ok:
        reason = []
        if missing:
            reason.append(", ".join(missing))
        if not mediapipe_ok:
            reason.append("MediaPipe hands API (mediapipe.solutions)")
        print(
            f"Syncing demo environment ({'; '.join(reason)})…",
            file=sys.stderr,
        )
        run(["uv", "sync"], cwd=DEMO_DIR)
        if not _mediapipe_solutions_ok():
            print(
                "MediaPipe still broken after uv sync. Try:\n"
                "  cd Demo && uv pip install 'mediapipe>=0.10.14,<=0.10.15'\n"
                "Ensure no other mediapipe package shadows the venv (conda/pip global).",
                file=sys.stderr,
            )
            sys.exit(1)


def _import_missing(module: str) -> bool:
    try:
        __import__(module)
        return False
    except ImportError:
        return True


def _mediapipe_solutions_ok() -> bool:
    try:
        import mediapipe as mp
    except ImportError:
        return False
    return hasattr(mp, "solutions") and hasattr(mp.solutions, "hands")


class Launcher:
    def __init__(self, *, port: int, no_browser: bool, force_build: bool) -> None:
        self.port = port
        self.no_browser = no_browser
        self.force_build = force_build
        self.current_mode = DEFAULT_MODE
        self.proc: subprocess.Popen | None = None
        self.browser_opened = False
        self._shutting_down = False

    def stop_dataflow(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            self.proc = None
            cleanup_orphaned_demo_processes()
            free_web_ui_port(self.port)
            return
        print("\nStopping dataflow…")
        _terminate_process_tree(self.proc)
        self.proc = None
        cleanup_orphaned_demo_processes()
        free_web_ui_port(self.port)

    def start_dataflow(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"Unknown mode: {mode}")
        self.current_mode = mode
        write_mode_current(mode)
        clear_mode_request()

        dataflow = prepare_dataflow(mode)

        print(f"\nCamControls UI — mode: {mode}")
        print(f"Dataflow: {dataflow}")

        ensure_dora_up()
        free_web_ui_port(self.port)

        if should_build(self.force_build, mode, dataflow):
            build_dataflow(dataflow)
            self.force_build = False

        env = _dora_env()
        env["AH_WEB_NO_BROWSER"] = "1"
        env["AH_LAUNCH_MODE"] = mode

        print("Starting dataflow (Ctrl+C to stop)…")
        popen_kw: dict = {"cwd": DEMO_DIR, "env": env}
        if hasattr(os, "killpg"):
            popen_kw["start_new_session"] = True
        self.proc = subprocess.Popen(
            ["dora", "run", dataflow, "--uv"],
            **popen_kw,
        )

        if not self.browser_opened:
            print(f"Waiting for web UI on port {self.port}…")
            if wait_for_web_ui(self.port):
                url = f"http://127.0.0.1:{self.port}/"
                print(f"Web UI ready: {url}")
                if not self.no_browser:
                    webbrowser.open(url)
                self.browser_opened = True
                print(
                    "Tracking is live. Use the browser to switch mode, pick a camera, "
                    "and open MuJoCo 3D views."
                )
            else:
                print(
                    "Web UI did not respond in time. Check dora logs; "
                    "dataflow may still be starting.",
                    file=sys.stderr,
                )

    def restart_if_requested(self) -> bool:
        requested = read_mode_request()
        if requested is None or requested == self.current_mode:
            return False
        print(f"Mode switch requested: {self.current_mode} → {requested}")
        self.stop_dataflow()
        self.start_dataflow(requested)
        return True

    def restart_if_serial_requested(self) -> bool:
        if self.current_mode not in REAL_MODES:
            launch_serial.clear_serial_request()
            return False
        requested = launch_serial.read_serial_request()
        if requested is None:
            return False
        current = launch_serial.read_serial_config()
        if not launch_serial.serial_config_changed(current, requested):
            launch_serial.clear_serial_request()
            return False
        if not requested.get("right"):
            print(
                "Serial change ignored: no right-hand port selected.",
                file=sys.stderr,
            )
            launch_serial.clear_serial_request()
            return False
        print(
            f"Serial change requested: right={requested['right']}"
            + (f" left={requested['left']}" if requested.get("left") else "")
        )
        self.stop_dataflow()
        launch_serial.write_serial_current(requested)
        launch_serial.clear_serial_request()
        self.start_dataflow(self.current_mode)
        return True

    def shutdown(self, signum=None, frame=None) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.stop_dataflow()
        cleanup_orphaned_demo_processes()
        free_web_ui_port(self.port)
        sys.exit(0)

    def run(self, initial_mode: str = DEFAULT_MODE) -> None:
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)

        launch_shutdown.clear_shutdown_request()
        self.start_dataflow(initial_mode)

        while not self._shutting_down:
            if launch_shutdown.read_shutdown_request():
                launch_shutdown.clear_shutdown_request()
                print("\nShutdown requested from web UI.")
                self.shutdown()

            if self.proc is not None and self.proc.poll() is not None:
                code = self.proc.returncode
                print(f"Dataflow exited (code {code}).")
                if launch_shutdown.read_shutdown_request():
                    launch_shutdown.clear_shutdown_request()
                    self.shutdown()
                requested = read_mode_request()
                if requested and requested != self.current_mode:
                    clear_mode_request()
                    self.start_dataflow(requested)
                    continue
                sys.exit(code or 0)

            self.restart_if_requested()
            self.restart_if_serial_requested()
            time.sleep(0.3)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch hand tracking demo (Dora + browser UI). "
        "Switch simulation vs real hardware from the web page.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AH_WEB_PORT", str(DEFAULT_PORT))),
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open browser automatically",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Force dora build even if a recent build stamp exists",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Stop orphaned demo processes (web UI, tracking, MuJoCo) and exit",
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup_orphaned_demo_processes()
        free_web_ui_port(args.port)
        print("Cleanup complete.")
        return

    def _atexit_cleanup() -> None:
        cleanup_orphaned_demo_processes(verbose=False)
        free_web_ui_port(args.port)

    atexit.register(_atexit_cleanup)

    ensure_demo_dependencies()

    launcher = Launcher(
        port=args.port,
        no_browser=args.no_browser,
        force_build=args.build,
    )
    launcher.run()


if __name__ == "__main__":
    main()
