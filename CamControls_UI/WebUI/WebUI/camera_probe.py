"""Probe OpenCV camera indices available on the host."""

import cv2


def probe_cameras(max_index: int = 10) -> list[dict]:
    """Return cameras that can be opened, as {index, label} dicts."""
    cameras = []
    for index in range(max_index):
        cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            continue
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        cameras.append(
            {
                "index": index,
                "label": f"Camera {index} ({width}x{height})",
            }
        )
    return cameras
