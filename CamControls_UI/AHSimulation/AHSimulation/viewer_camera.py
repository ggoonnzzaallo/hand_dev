"""Frame the MuJoCo free camera on the hand model."""

import os
import shutil
import subprocess
import sys
import time

import mujoco
import numpy as np

_DEFAULT_VIEWER_WIDTH = 420
_DEFAULT_VIEWER_HEIGHT = 560
# Framing slack around wrist + tips at open pose; higher = zoomed out (room for motion).
_DEFAULT_CAMERA_PADDING = 1.55
# Extra distance so extended fingers during tracking stay in frame (static framing at open).
_CAMERA_MOTION_SCALE = 1.2
# Deferred window-fit state keyed by id(viewer).
_PENDING_WINDOW_FIT: dict[int, dict] = {}


def _libpython_directory() -> str | None:
    """Directory containing libpython*.dylib (needed for mjpython + uv-managed CPython)."""
    real_exe = os.path.realpath(sys.executable)
    candidates = [
        os.path.join(os.path.dirname(os.path.dirname(real_exe)), "lib"),
        os.path.join(sys.prefix, "lib"),
    ]
    for lib_dir in candidates:
        if not os.path.isdir(lib_dir):
            continue
        for name in os.listdir(lib_dir):
            if name.startswith("libpython") and name.endswith(".dylib"):
                return lib_dir
    return None


def ensure_mjpython_on_macos() -> None:
    """Relaunch under mjpython on macOS (required for launch_passive viewer)."""
    if sys.platform != "darwin":
        return
    if os.environ.get("AH_MJ_REEXEC") == "1":
        return
    if os.path.basename(sys.executable) == "mjpython":
        return

    env = dict(os.environ)
    env["AH_MJ_REEXEC"] = "1"

    lib_dir = _libpython_directory()
    if lib_dir:
        paths = [lib_dir]
        old = env.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        if old:
            paths.append(old)
        else:
            paths.extend(("/usr/local/lib", "/usr/lib"))
        env["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(paths)

    bindir = os.path.dirname(os.path.realpath(sys.executable))
    mjpython_path = os.path.join(bindir, "mjpython")
    if os.path.isfile(mjpython_path):
        os.execve(mjpython_path, [mjpython_path, *sys.argv], env)
    os.execvpe("mjpython", ["mjpython", *sys.argv], env)


def _camera_padding() -> float:
    raw = os.environ.get("AH_MJ_CAMERA_PADDING")
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_CAMERA_PADDING


def frame_hand_camera(
    viewer,
    model: mujoco.MjModel,
    data: mujoco.MjData,
    wrist_body: str,
    *,
    padding: float | None = None,
    azimuth: float = 145.0,
    elevation: float = -18.0,
) -> None:
    """Point the passive viewer camera at the hand with comfortable framing."""
    if padding is None:
        padding = _camera_padding()
    mujoco.mj_forward(model, data)

    wrist_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, wrist_body)
    points = [data.xpos[wrist_id].copy()]
    for i in range(1, 5):
        site_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, f"tip{i}")
        points.append(data.site_xpos[site_id].copy())

    pts = np.asarray(points)
    center = pts.mean(axis=0)
    radius = np.linalg.norm(pts - center, axis=1).max() * padding

    # Distance so the hand bounding sphere fits in view (~45 deg vertical FOV).
    distance = max(radius / np.tan(np.deg2rad(22.5)), 0.12) * _CAMERA_MOTION_SCALE

    viewer.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
    viewer.cam.lookat[:] = center
    viewer.cam.distance = distance
    viewer.cam.azimuth = azimuth
    viewer.cam.elevation = elevation
    viewer.sync()


def disable_viewer_reflections(viewer, model: mujoco.MjModel) -> None:
    """Turn off reflective rendering to reduce GPU load on weaker machines."""
    viewer.opt.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = False

    ground_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_MATERIAL, "groundplane"
    )
    if ground_id >= 0:
        model.mat_reflectance[ground_id] = 0.0

    viewer.sync()


def hide_viewer_ui_panels(viewer) -> None:
    """Hide Simulate side panels and suppress the PAUSE top overlay when possible."""
    sim = viewer._sim()  # noqa: SLF001 — MuJoCo Handle internal weakref
    if sim is not None:
        sim.ui0_enable = False
        sim.ui1_enable = False
        sim.clear_texts()
        # Passive viewer defaults to run=1; overlay appears when run=0. Bindings expose
        # run as read-only, so this is best-effort for future MuJoCo versions.
        try:
            sim.run = True  # noqa: B010 — MuJoCo uses int/bool for run state
        except AttributeError:
            pass
    viewer.sync()


def _viewer_size_from_env() -> tuple[int, int]:
    width = int(os.environ.get("AH_MJ_VIEWER_WIDTH", str(_DEFAULT_VIEWER_WIDTH)))
    height = int(os.environ.get("AH_MJ_VIEWER_HEIGHT", str(_DEFAULT_VIEWER_HEIGHT)))
    return max(200, width), max(200, height)


def _window_fit_enabled() -> bool:
    return os.environ.get("AH_MJ_WINDOW_FIT", "").lower() in ("1", "true", "yes")


def _resize_mujoco_window_macos(width: int, height: int) -> None:
    script = f"""
    tell application "System Events"
      repeat with p in (every process whose background only is false)
        try
          repeat with w in (every window of p whose name starts with "MuJoCo")
            set position of w to {{100, 80}}
            set size of w to {{{width}, {height}}}
          end repeat
        end try
      end repeat
    end tell
    """
    subprocess.run(
        ["osascript", "-e", script],
        check=False,
        capture_output=True,
    )


def _resize_mujoco_window_linux(width: int, height: int) -> None:
    xdotool = shutil.which("xdotool")
    if not xdotool:
        return
    result = subprocess.run(
        [xdotool, "search", "--name", "MuJoCo"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in result.stdout.strip().splitlines():
        wid = line.strip()
        if wid:
            subprocess.run(
                [xdotool, "windowsize", wid, str(width), str(height)],
                check=False,
                capture_output=True,
            )


def _sync_viewer_after_resize(viewer) -> None:
    """Ask Simulate to pick up the new framebuffer size after an external window resize."""
    sim = viewer._sim()  # noqa: SLF001
    if sim is None:
        viewer.sync()
        return
    hide_viewer_ui_panels(viewer)
    for _ in range(10):
        try:
            sim.sync()
        except Exception:
            pass
        viewer.sync()
        time.sleep(0.05)


def _apply_viewer_window_size(width: int, height: int, viewer) -> None:
    if sys.platform == "darwin":
        _resize_mujoco_window_macos(width, height)
    elif sys.platform.startswith("linux"):
        _resize_mujoco_window_linux(width, height)
    _sync_viewer_after_resize(viewer)


def fit_viewer_window(
    viewer,
    *,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """Schedule a one-shot compact resize after the GLFW window exists.

    Off by default: set AH_MJ_WINDOW_FIT=1. Automatic resize can leave a black
    band above the 3D view on macOS; leaving the default MuJoCo size avoids that.
    """
    if not _window_fit_enabled():
        return
    if width is None or height is None:
        env_w, env_h = _viewer_size_from_env()
        width = width if width is not None else env_w
        height = height if height is not None else env_h

    viewer.sync()
    _PENDING_WINDOW_FIT[id(viewer)] = {"ticks": 0, "width": width, "height": height}


def tick_viewer_window_fit(viewer) -> None:
    """Run once per simulation tick; performs deferred window resize."""
    state = _PENDING_WINDOW_FIT.get(id(viewer))
    if state is None:
        return

    state["ticks"] += 1
    if state["ticks"] < 25:
        return

    width = state["width"]
    height = state["height"]
    del _PENDING_WINDOW_FIT[id(viewer)]
    _apply_viewer_window_size(width, height, viewer)


def configure_minimal_viewer(viewer, model: mujoco.MjModel) -> None:
    """Hide UI chrome and use a simple flat background (hand-only view)."""
    viewer.opt.flags[mujoco.mjtRndFlag.mjRND_REFLECTION] = False
    viewer.opt.flags[mujoco.mjtRndFlag.mjRND_SHADOW] = False
    viewer.opt.flags[mujoco.mjtRndFlag.mjRND_SKYBOX] = False
    viewer.opt.flags[mujoco.mjtRndFlag.mjRND_HAZE] = False

    ground_id = mujoco.mj_name2id(
        model, mujoco.mjtObj.mjOBJ_MATERIAL, "groundplane"
    )
    if ground_id >= 0:
        model.mat_reflectance[ground_id] = 0.0

    hide_viewer_ui_panels(viewer)
