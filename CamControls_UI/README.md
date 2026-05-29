# CamControls UI

Webcam hand tracking and control for the Pollen **Amazing Hand** (simulation + optional real hardware). One command starts the browser UI, Dora pipeline, and tracking.

This folder is the application root inside the `hand_dev` repository.

## Prerequisites (one time per machine)

| Tool | Install |
|------|---------|
| **uv** | https://docs.astral.sh/uv/getting-started/installation/ |
| **Dora CLI** | `uv tool install "dora-rs-cli==0.3.13"` |
| **Rust** | https://www.rust-lang.org/tools/install — only for **real hardware** pipeline modes |

## Run

```bash
cd CamControls_UI
uv run launch
```

Opens http://127.0.0.1:8765 (use `--no-browser` to skip auto-open).

The launcher installs Python deps if needed, starts the Dora daemon if it is not running, and builds the dataflow on first run. No need to run `uv sync` or `dora up` manually each time.

## Web UI

- **Camera** — OpenCV device index
- **Pipeline** — simulation vs real hardware (restarts ~10s; real modes enable servo torque)
- **MuJoCo 3D view** — optional viewer windows (no restart)
- **Serial (hardware)** — USB port + servo bus scan
- **Shutdown** — stops the demo and exits the terminal process

See [GONZALO_NOTES.md](GONZALO_NOTES.md) for troubleshooting, motor IDs, and MuJoCo viewer notes.

## Hand hardware

Motor layout and `AHControl` tools: see [AHControl/README.md](AHControl/README.md) and [docs/](docs/).

Upstream hand CAD/firmware repo: [Pollen Robotics AmazingHand](https://github.com/pollen-robotics/AmazingHand) (this project is a standalone control UI extracted from its `Demo/` folder).

## Cleanup

```bash
uv run launch --cleanup
```
