import argparse
import signal
import time

import cv2
import numpy as np
import pyarrow as pa
from dora import Node
import mediapipe as mp
from scipy.spatial.transform import Rotation

mp_drawing = mp.solutions.drawing_utils
mp_drawing_styles = mp.solutions.drawing_styles
mp_hands = mp.solutions.hands

# https://mediapipe.readthedocs.io/en/latest/solutions/hands.html

PREVIEW_MAX_WIDTH = 640
PREVIEW_JPEG_QUALITY = 75


def encode_preview_jpeg(frame: np.ndarray) -> bytes:
    h, w = frame.shape[:2]
    if w > PREVIEW_MAX_WIDTH:
        scale = PREVIEW_MAX_WIDTH / w
        frame = cv2.resize(
            frame,
            (PREVIEW_MAX_WIDTH, int(h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(
        ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), PREVIEW_JPEG_QUALITY]
    )
    if not ok:
        return b""
    return buf.tobytes()


def open_camera(index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open camera index {index}")
    return cap


def process_img(hand_proc, image):
    image.flags.writeable = False
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    results = hand_proc.process(image)
    image.flags.writeable = True
    image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    r_res = None
    l_res = None
    if results.multi_hand_landmarks:
        for index, handedness_classif in enumerate(results.multi_handedness):
            if handedness_classif.classification[0].score > 0.8:
                label = handedness_classif.classification[0].label

                hand_landmarks = results.multi_hand_world_landmarks[index]
                hand_landmarks_norm = results.multi_hand_landmarks[index]

                tip1_x = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP].x
                    - hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_MCP].x
                )
                tip1_y = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP].y
                    - hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_MCP].y
                )
                tip1_z = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_TIP].z
                    - hand_landmarks.landmark[mp_hands.HandLandmark.INDEX_FINGER_MCP].z
                )

                tip2_x = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_TIP].x
                    - hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_MCP].x
                )
                tip2_y = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_TIP].y
                    - hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_MCP].y
                )
                tip2_z = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_TIP].z
                    - hand_landmarks.landmark[mp_hands.HandLandmark.MIDDLE_FINGER_MCP].z
                )

                tip3_x = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_TIP].x
                    - hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_MCP].x
                )
                tip3_y = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_TIP].y
                    - hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_MCP].y
                )
                tip3_z = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_TIP].z
                    - hand_landmarks.landmark[mp_hands.HandLandmark.RING_FINGER_MCP].z
                )

                tip4_x = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].x
                    - hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_MCP].x
                )
                tip4_y = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].y
                    - hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_MCP].y
                )
                tip4_z = (
                    hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_TIP].z
                    - hand_landmarks.landmark[mp_hands.HandLandmark.THUMB_MCP].z
                )

                mp_drawing.draw_landmarks(
                    image,
                    hand_landmarks_norm,
                    mp_hands.HAND_CONNECTIONS,
                    mp_drawing_styles.get_default_hand_landmarks_style(),
                    mp_drawing_styles.get_default_hand_connections_style(),
                )

                origin = np.array(
                    [
                        hand_landmarks_norm.landmark[mp_hands.HandLandmark.WRIST].x,
                        hand_landmarks_norm.landmark[mp_hands.HandLandmark.WRIST].y,
                        hand_landmarks_norm.landmark[mp_hands.HandLandmark.WRIST].z,
                    ]
                )
                mid_mcp = np.array(
                    [
                        hand_landmarks_norm.landmark[
                            mp_hands.HandLandmark.MIDDLE_FINGER_MCP
                        ].x,
                        hand_landmarks_norm.landmark[
                            mp_hands.HandLandmark.MIDDLE_FINGER_MCP
                        ].y,
                        hand_landmarks_norm.landmark[
                            mp_hands.HandLandmark.MIDDLE_FINGER_MCP
                        ].z,
                    ]
                )
                unit_z = mid_mcp - origin
                unit_z = unit_z / np.linalg.norm(unit_z)
                pinky_mcp = np.array(
                    [
                        hand_landmarks_norm.landmark[mp_hands.HandLandmark.PINKY_MCP].x,
                        hand_landmarks_norm.landmark[mp_hands.HandLandmark.PINKY_MCP].y,
                        hand_landmarks_norm.landmark[mp_hands.HandLandmark.PINKY_MCP].z,
                    ]
                )
                index_mcp = np.array(
                    [
                        hand_landmarks_norm.landmark[
                            mp_hands.HandLandmark.INDEX_FINGER_MCP
                        ].x,
                        hand_landmarks_norm.landmark[
                            mp_hands.HandLandmark.INDEX_FINGER_MCP
                        ].y,
                        hand_landmarks_norm.landmark[
                            mp_hands.HandLandmark.INDEX_FINGER_MCP
                        ].z,
                    ]
                )

                if label == "Right":
                    vec_towards_y = pinky_mcp - origin
                if label == "Left":
                    vec_towards_y = index_mcp - origin

                unit_x = np.cross(vec_towards_y, unit_z)
                unit_x = unit_x / np.linalg.norm(unit_x)
                unit_y = np.cross(unit_z, unit_x)

                if label == "Right":
                    R = np.array([unit_x, -unit_y, unit_z]).reshape((3, 3))
                if label == "Left":
                    R = np.array([unit_x, -unit_y, unit_z]).reshape((3, 3))
                tip1 = R @ np.array([tip1_x, tip1_y, tip1_z])
                tip2 = R @ np.array([tip2_x, tip2_y, tip2_z])
                tip3 = R @ np.array([tip3_x, tip3_y, tip3_z])
                tip4 = R @ np.array([tip4_x, tip4_y, tip4_z])

                if label == "Right":
                    r_res = [
                        {
                            "r_tip1": tip1,
                            "r_tip2": tip2,
                            "r_tip3": tip3,
                            "r_tip4": tip4,
                        }
                    ]
                elif label == "Left":
                    l_res = [
                        {
                            "l_tip1": tip1,
                            "l_tip2": tip2,
                            "l_tip3": tip3,
                            "l_tip4": tip4,
                        }
                    ]
    return image, r_res, l_res


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="OpenCV camera index (default: 0)",
    )
    parser.add_argument(
        "--swap-handedness",
        action="store_true",
        help="Swap MediaPipe Left/Right labels (only if tracking drives the wrong hand)",
    )
    args = parser.parse_args()

    node = Node()
    pa.array([])

    camera_index = args.camera
    cap = open_camera(camera_index)
    read_failures = 0

    def release_camera(*_args) -> None:
        try:
            cap.release()
        except Exception:
            pass

    signal.signal(signal.SIGTERM, release_camera)
    signal.signal(signal.SIGINT, release_camera)

    with mp_hands.Hands(
        model_complexity=0,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ) as hands:
        for event in node:
            event_type = event["type"]

            if event_type == "INPUT":
                event_id = event["id"]

                if event_id == "set_camera":
                    new_index = int(event["value"][0].as_py())
                    if new_index != camera_index:
                        cap.release()
                        camera_index = new_index
                        cap = open_camera(camera_index)

                elif event_id == "tick":
                    ret, frame = cap.read()
                    if not ret:
                        read_failures += 1
                        if read_failures >= 30:
                            cap.release()
                            cap = open_camera(camera_index)
                            read_failures = 0
                        continue
                    read_failures = 0

                    frame = cv2.flip(frame, 1)
                    frame, r_res, l_res = process_img(hands, frame)
                    if args.swap_handedness:
                        r_res, l_res = l_res, r_res

                    if r_res is not None:
                        node.send_output("r_hand_pos", pa.array(r_res))
                    if l_res is not None:
                        node.send_output("l_hand_pos", pa.array(l_res))

                    jpeg = encode_preview_jpeg(frame)
                    if jpeg:
                        node.send_output("preview_jpeg", pa.array([jpeg]))

            elif event_type == "ERROR":
                raise RuntimeError(event["error"])

    cap.release()


if __name__ == "__main__":
    main()
