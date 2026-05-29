# CamControls UI — notes

## Local Setup

- Install Rust: https://www.rust-lang.org/tools/install
- Install uv: https://docs.astral.sh/uv/getting-started/installation/
- Install Dora CLI (version compatible with this demo):
  - `uv tool install --reinstall "dora-rs-cli==0.3.13"`

From the `CamControls_UI/` directory:

```bash
uv run launch
```

On first run, `uv` installs Python 3.12 (see `.python-version`) and syncs the workspace: launcher (`launch`, `launch_serial`, …), **AHSimulation**, **HandTracking**, and **WebUI**. Dora runs each node with `uv run python` from that `.venv`.

Use `uv run ...` from `CamControls_UI/`; do not manually activate a virtualenv.

If nodes fail with `No module named 'mujoco'`, `cv2`, or `pyarrow`, run `uv sync` once from `CamControls_UI/`, or let `uv run launch` auto-sync when it detects missing packages.

### `mediapipe` has no attribute `solutions`

The real failure is **`hand_tracker`** (MediaPipe). The `web_ui` / simulation `RuntimeError: Could not initiate node from environment variable` lines are a **cascade** — Dora stops when an upstream node dies.

Usually causes:

1. **Broken or racing install** — Several Dora nodes start at once; each `uv run` used to re-sync the venv and could leave MediaPipe half-installed. `uv run launch` now sets `UV_NO_SYNC=1` for Dora and checks `mediapipe.solutions` before start.
2. **Wrong MediaPipe** — Need Google's `mediapipe` **0.10.14–0.10.15** (legacy `solutions.hands` API). Not a random PyPI stub or a too-new version without `solutions`.

Fix:

```bash
cd hand_dev
uv sync
uv run python -c "import mediapipe as mp; print(mp.__version__, mp.solutions.hands)"
uv run launch
```

If that one-liner fails, reinstall MediaPipe in the project venv only: `uv pip install 'mediapipe>=0.10.14,<=0.10.15'`. Avoid mixing conda/global `pip install mediapipe` with this demo.

## Run Demos (recommended)

One command starts Dora, builds if needed, runs the dataflow, and opens the browser:

```bash
cd hand_dev
source "$HOME/.cargo/env"   # if using conda/base and cargo is missing
uv run launch
```

Optional flags:

- `uv run launch --build` — force `dora build` even if a recent build stamp exists
- `uv run launch --no-browser` — do not open the browser automatically
- `uv run launch --cleanup` — kill orphaned demo processes (Web UI, tracking, MuJoCo, AHControl) and exit
- `AH_WEB_PORT=8766 uv run launch` — change the web UI port

Press **Ctrl+C** to stop. The launcher stops the full `dora run` process group and cleans up stray node processes.

### What happens

1. Dora daemon starts (if not already running).
2. Dataflow builds on first run (or when YAML changes); later runs skip build by default (`CamControls_UI/out/.dora_build_ok`).
3. **Simulation** mode starts by default (no serial port, no motor torque).
4. Browser opens at http://127.0.0.1:8765.
5. Use the web page to set **Pipeline**, **Camera**, **MuJoCo 3D view**, and **Serial (hardware)**.

Runtime dataflows for real hardware are written to `CamControls_UI/dataflow_tracking_real*_runtime.yml` (not under `out/`) so Dora can resolve package paths correctly.

### Pipeline vs MuJoCo 3D view

These are **two separate controls** (not six combined modes):

| Control | Restarts pipeline? | What it does |
|---------|-------------------|--------------|
| **Pipeline** buttons | Yes (~10s) | Switches Dora dataflow: tracking + IK; real modes add `hand_controller` (servos) |
| **MuJoCo 3D view** buttons | No | Opens an optional MuJoCo window for right / left / both simulation nodes |

### Pipeline modes (browser)

| Pipeline | What runs |
|----------|-----------|
| **Simulation — both hands** | Both hands tracked + both IK nodes; no `hand_controller`; serial not opened |
| **Real hardware — right hand** | Right tracking + right IK + right servos (`r_hand.toml`, IDs 1–8) |
| **Real hardware — both hands** | Both tracked + both IK + servos (`2hands.toml` or dual USB) |

There is **no** “real left hand only” pipeline — left hardware is only via **Real hardware — both hands**.

Switching pipeline restarts Dora (~10s). The webcam preview may freeze briefly; hard-refresh or `uv run launch --cleanup` if it stays stuck.

**Warning:** Real pipeline modes enable motor torque and move the hand to its home pose on startup.

### MuJoCo 3D view (browser)

| Button | Enabled when |
|--------|----------------|
| **Right hand** | All pipeline modes (right IK node always present in these graphs) |
| **Left hand** / **Both hands** | Simulation or Real — both hands only (left IK node not in real-right pipeline) |

IK keeps running headless; these buttons only open viewer windows.

## Browser control panel

- **Webcam preview** — live MJPEG with landmarks (no separate OpenCV window).
- **Camera** — OpenCV device index (`0`, `1`, …). Saved in `CamControls_UI/out/camera_current.json`; applied when the pipeline restarts.
- **Pipeline** — simulation vs real hardware (restarts ~10s).
- **MuJoCo 3D view** — optional viewer windows (no restart).
- **Serial (hardware)** — USB port picker (de-emphasized during simulation; still configurable). See below.
- **Shutdown** — stops Dora, cleans up processes, and exits `uv run launch` in the terminal (same as Ctrl+C). Request is written to `CamControls_UI/out/shutdown_request.json`.

Footer: Built by [Gonzalo Graham](https://gonzalobuilds.com/).

## Serial (hardware)

Ports are **auto-detected** and listed in the browser (refreshed periodically):

- **macOS:** `/dev/cu.usbmodem*` (and similar). Use **`cu.*`** for communication, not `tty.*`.
- **Linux:** `/dev/ttyACM*`, `/dev/ttyUSB*`

| UI control | Meaning |
|------------|---------|
| **Right hand** | Primary USB device. Required for real modes. |
| **Left hand** (Real — both hands only) | Default: **Same USB as right** — both hands on one bus (`AHControl/config/2hands.toml`, motor IDs 1–8 and 11–18). |
| **Left hand** = second port | Only if each hand has its **own** USB adapter. Launcher splits into two `AHControl` nodes, each with `r_hand.toml`. |

Changing a serial dropdown restarts the pipeline (~10s) in real modes. In simulation mode, the choice is **saved** for the next real-mode start.

- **Bus scan** — Use **Scan servos** in the UI (once on first load if a port is saved; again after you change port or when you click the button). Pings at **1 Mbps** and shows IDs, voltage/temperature, and layout vs `r_hand.toml` / `2hands.toml`. Use **Simulation** mode while scanning; the port is locked during real hardware.

- Saved choice: `CamControls_UI/out/serial_current.json`
- Pending restart: `CamControls_UI/out/serial_request.json`
- Mode state: `CamControls_UI/out/mode_current.json`, `CamControls_UI/out/mode_request.json`

Override the UI with an environment variable:

```bash
AH_SERIAL_PORT=/dev/cu.usbmodem5B141111901 uv run launch
```

### Right-hand servo IDs (Pollen default)

| Finger | Servo IDs |
|--------|-----------|
| Index  | 1, 2 |
| Middle | 3, 4 |
| Ring   | 5, 6 |
| Thumb  | 7, 8 |

Config: `AHControl/config/r_hand.toml`. A bus scan with `rustypot` should show IDs **1–8** responding on a standalone right hand.

For **two hands on one USB bus**, use `2hands.toml` (left-hand IDs **11–18** on the same port).

## Rust / AHControl build

Real modes require `CamControls_UI/target/debug/AHControl`. The launcher runs `cargo build` automatically when needed.

```bash
source "$HOME/.cargo/env"
cd hand_dev && cargo build -p AHControl
```

If you use conda `(base)`, `cargo` may not be on PATH until you `source "$HOME/.cargo/env"` or open a new terminal.

## MuJoCo viewer

Opened from **3D preview** buttons (not on pipeline start). On macOS the simulation node re-execs under **`mjpython`** (required for the passive viewer).

- Side panels hidden (`show_left_ui=False`, `show_right_ui=False`; press **Tab** to toggle if needed).
- Default window size is MuJoCo’s built-in size (avoids a black band above the 3D view on macOS).
- Optional compact window: `AH_MJ_WINDOW_FIT=1` (~420×560 via AppleScript on macOS, after ~25 ticks).
- Env overrides when fitting: `AH_MJ_VIEWER_WIDTH`, `AH_MJ_VIEWER_HEIGHT`
- Hand-focused camera framing (zoomed out enough for finger motion); flat gray background (no skybox/reflections). Override: `AH_MJ_CAMERA_PADDING=1.7` (higher = farther).

### Black bar at the top of the MuJoCo window

Usually one of these (on macOS):

1. **macOS title bar** — Dark strip with “MuJoCo” and window controls. Normal window chrome; MuJoCo does not expose a borderless mode from Python.
2. **Letterboxing after compact resize** — Do **not** set `AH_MJ_WINDOW_FIT=1`, or resize the window manually once.
3. **PAUSE overlay** — Large “PAUSE” text when paused. Press **Space** in the viewer.

## Stopping demos and orphaned processes

`uv run launch` sends Ctrl+C to the whole `dora run` process group, then kills common stragglers (`WebUI`, `HandTracking`, `mj_mink_*`, `mjpython`, `AHControl`).

If Activity Monitor still shows leftover `python` / `mjpython` after quitting:

```bash
cd hand_dev
uv run launch --cleanup
```

Or manually:

```bash
lsof -iTCP:8765 -sTCP:LISTEN    # stale web UI on default port
dora destroy                     # full Dora reset if daemon is stuck
dora up
```

`--cleanup` does **not** stop the Dora daemon/coordinator (keeps `dora up` fast for the next run).

## Real hand not moving (troubleshooting)

Pipeline: **webcam → MediaPipe → IK (`mj_mink_right`) → `AHControl` → USB serial → motors**.

### 1. Tracking in the browser

- Use **Real — right hand** and show your **physical right hand** to the camera.
- Landmarks should appear on the MJPEG preview.
- Try another **Camera** index or better lighting if none appear.

### 2. Wrong hand tracked (Left vs Right)

The preview is mirrored (`cv2.flip`). If the wrong hand drives the robot or sim, add to `hand_tracker` args in the dataflow YAML (then `uv run launch --build`):

```yaml
args: --camera 0 --swap-handedness
```

Rebuild or delete `CamControls_UI/out/.dora_build_ok` to force `dora build`.

### 3. `hand_controller` logs

Latest run: `CamControls_UI/out/<uuid>/log_hand_controller.txt`

- Expect: `metadata: name: "r_finger1" idx [0, 1] data: …`
- **data** should change by roughly **0.05–0.5** rad when you move your hand, not stay near `1e-5`.
- Metadata missing → commands not reaching the controller.
- Data changes but hand still → **power**, wrong **serial port**, or motor IDs.

### 4. Serial port / power

- Pick the correct device in **Serial (hardware)** (not another `usbmodem` device on the desk).
- Hand needs a **servo power supply**; USB often only powers the adapter logic.
- Verify motors respond:

```bash
cd hand_dev
uv run python -c "
from rustypot import Scs0009PyController
import glob
port = sorted(glob.glob('/dev/cu.usbmodem*'))[0]
c = Scs0009PyController(serial_port=port, baudrate=1000000, timeout=0.3)
print('Port', port, 'IDs', [i for i in range(1, 9) if c.ping(i)])
"
```

### 5. Quick motor test (no webcam / Dora)

```bash
cd hand_dev
source "$HOME/.cargo/env"
./target/debug/AHControl --serialport /dev/cu.usbmodemYOURPORT --config AHControl/config/r_hand.toml
```

Torque on and brief homing on start; Ctrl+C to stop.

Gesture demo (Pollen script; edit port and `MiddlePos` in file):

```bash
uv run python pollen_hand.py
```

## Manual run (advanced)

```bash
dora up
dora build dataflow_tracking_simu.yml --uv
dora run dataflow_tracking_simu.yml --uv
```

Synthetic finger-angle demo (no browser UI):

```bash
dora build dataflow_angle_simu.yml --uv
dora run dataflow_angle_simu.yml --uv
```

## Key files

| Path | Role |
|------|------|
| `launch.py` | Unified launcher, mode/serial restart, process cleanup |
| `launch_serial.py` | Serial port persistence and dataflow patching |
| `WebUI/WebUI/serial_probe.py` | List USB serial devices |
| `dataflow_tracking_*.yml` | Dora graphs (sim / real / 2hands) |
| `dataflow_tracking_*_runtime.yml` | Generated at run time (serial ports patched) |
| `AHControl/config/r_hand.toml` | Motor IDs and offsets (right hand) |
| `AHControl/config/2hands.toml` | Both hands on one bus |
| `HandTracking/HandTracking/main.py` | Webcam + MediaPipe |
| `AHSimulation/AHSimulation/mj_mink_right.py` | Right-hand IK + MuJoCo viewer |

## macOS notes

- MuJoCo simulation nodes re-exec under **`mjpython`** (required for `launch_passive` on macOS). With **uv**’s managed Python, `mjpython` needs `libpython3.12.dylib` on `DYLD_FALLBACK_LIBRARY_PATH` — `viewer_camera.ensure_mjpython_on_macos()` sets that automatically.
- If you see `Library not loaded: libpython3.12.dylib` in `l_hand_simulation` / `r_hand_simulation`, run `uv sync` from `CamControls_UI/` and pull the latest `viewer_camera.py` fix, then retry.
- Prefer `/dev/cu.usbmodem*` for the hand, not `/dev/ttyACM0` (Linux placeholder in YAML templates).
