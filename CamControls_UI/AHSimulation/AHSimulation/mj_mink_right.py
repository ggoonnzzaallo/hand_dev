"""Mujoco Client: This node is used to represent simulated robot, it can be used to read virtual positions, or can be controlled."""

import argparse

import os
import signal
import sys
import time

import mujoco
import mujoco.viewer
import pyarrow as pa
from dora import Node

import mink
from loop_rate_limiters import RateLimiter
from mink.contrib import TeleopMocap
from pathlib import Path
import numpy as np

from viewer_camera import (
    configure_minimal_viewer,
    ensure_mjpython_on_macos,
    fit_viewer_window,
    frame_hand_camera,
    tick_viewer_window_fit,
)

ROOT_PATH = Path(os.path.dirname(os.path.abspath(__file__))).parent
WRIST_BODY = "r_wrist_interface"


class Client:
    """TODO: Add docstring."""

    def __init__(self, mode='pos'):
        """TODO: Add docstring."""


        self.model = mujoco.MjModel.from_xml_path(
            f"{ROOT_PATH}/AHSimulation/AH_Right/mjcf/scene.xml"
        )
        # self.data=mujoco.MjData(self.model)


        self.configuration = mink.Configuration(self.model)

        self.posture_task = mink.PostureTask(self.model, cost=1e-2)

        if mode=='pos':
            self.task1 = mink.FrameTask(
                frame_name='tip1',
                frame_type="site",
                position_cost=1.0,
                orientation_cost=0.0,
                lm_damping=1.0,
            )

            self.task2 = mink.FrameTask(
                frame_name='tip2',
                frame_type="site",
                position_cost=1.0,
                orientation_cost=0.0,
                lm_damping=1.0,
            )

            self.task3 = mink.FrameTask(
                frame_name='tip3',
                frame_type="site",
                position_cost=1.0,
                orientation_cost=0.0,
                lm_damping=1.0,
            )

            self.task4 = mink.FrameTask(
                frame_name='tip4',
                frame_type="site",
                position_cost=1.0,
                orientation_cost=0.0,
                lm_damping=1.0,
            )
        elif mode=='quat': #we control the orientation
            self.task1 = mink.FrameTask(
                frame_name='tip1',
                frame_type="site",
                position_cost=0.0,
                orientation_cost=1.0,
                lm_damping=1.0,
            )

            self.task2 = mink.FrameTask(
                frame_name='tip2',
                frame_type="site",
                position_cost=0.0,
                orientation_cost=1.0,
                lm_damping=1.0,
            )

            self.task3 = mink.FrameTask(
                frame_name='tip3',
                frame_type="site",
                position_cost=0.0,
                orientation_cost=1.0,
                lm_damping=1.0,
            )

            self.task4 = mink.FrameTask(
                frame_name='tip4',
                frame_type="site",
                position_cost=0.0,
                orientation_cost=1.0,
                lm_damping=1.0,
            )
        else:
            print(f"Error, unknown mode: {mode}")
            return -1
        # Regulate all equality constraints with the same cost.
        eq_task = mink.EqualityConstraintTask(self.model, cost=1000.0)

        self.tasks = [
            eq_task,
            self.posture_task,
            self.task1,
            self.task2,
            self.task3,
            self.task4,
        ]



        self.model = self.configuration.model
        self.data = self.configuration.data
        self.solver = "quadprog"

        self.motor_pos=[]
        self.metadata=[]
        self.node = Node()
        self.viewer = None
        self._viewer_ctx = None
        self._viewer_opened = False

    def _init_scene(self) -> None:
        self.configuration.update_from_keyframe("zero")
        self.posture_task.set_target_from_configuration(self.configuration)
        mink.move_mocap_to_frame(self.model, self.data, "finger1_target", "tip1", "site")
        mink.move_mocap_to_frame(self.model, self.data, "finger2_target", "tip2", "site")
        mink.move_mocap_to_frame(self.model, self.data, "finger3_target", "tip3", "site")
        mink.move_mocap_to_frame(self.model, self.data, "finger4_target", "tip4", "site")

    def _open_viewer(self) -> None:
        if self._viewer_opened:
            return
        self._viewer_ctx = mujoco.viewer.launch_passive(
            self.model,
            self.data,
            show_left_ui=False,
            show_right_ui=False,
        )
        self.viewer = self._viewer_ctx.__enter__()
        configure_minimal_viewer(self.viewer, self.model)
        frame_hand_camera(self.viewer, self.model, self.data, WRIST_BODY)
        fit_viewer_window(self.viewer)
        self._viewer_opened = True

    def _step_ik(self, rate: RateLimiter, metadata: dict) -> None:
        self.task1.set_target(
            mink.SE3.from_mocap_name(self.model, self.data, "finger1_target")
        )
        self.task2.set_target(
            mink.SE3.from_mocap_name(self.model, self.data, "finger2_target")
        )
        self.task3.set_target(
            mink.SE3.from_mocap_name(self.model, self.data, "finger3_target")
        )
        self.task4.set_target(
            mink.SE3.from_mocap_name(self.model, self.data, "finger4_target")
        )
        vel = mink.solve_ik(self.configuration, self.tasks, rate.dt, self.solver, 1e-5)
        self.configuration.integrate_inplace(vel, rate.dt)

        f1_motor1 = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "finger1_motor1")
        f1_motor2 = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "finger1_motor2")
        f2_motor1 = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "finger2_motor1")
        f2_motor2 = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "finger2_motor2")
        f3_motor1 = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "finger3_motor1")
        f3_motor2 = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "finger3_motor2")
        f4_motor1 = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "finger4_motor1")
        f4_motor2 = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, "finger4_motor2")
        self.metadata = metadata
        self.metadata["r_finger1"] = [0, 1]
        self.metadata["r_finger2"] = [2, 3]
        self.metadata["r_finger3"] = [4, 5]
        self.metadata["r_finger4"] = [6, 7]
        self.motor_pos = np.zeros(8)
        self.motor_pos[self.metadata["r_finger1"]] = np.array(
            [self.data.joint(f1_motor1).qpos[0], self.data.joint(f1_motor2).qpos[0]]
        )
        self.motor_pos[self.metadata["r_finger2"]] = np.array(
            [self.data.joint(f2_motor1).qpos[0], self.data.joint(f2_motor2).qpos[0]]
        )
        self.motor_pos[self.metadata["r_finger3"]] = np.array(
            [self.data.joint(f3_motor1).qpos[0], self.data.joint(f3_motor2).qpos[0]]
        )
        self.motor_pos[self.metadata["r_finger4"]] = np.array(
            [self.data.joint(f4_motor1).qpos[0], self.data.joint(f4_motor2).qpos[0]]
        )

    def run(self):
        """Run IK headless; open MuJoCo viewer only after launch_viewer input."""
        rate = RateLimiter(frequency=1000.0)
        self._init_scene()

        try:
            for event in self.node:
                event_type = event["type"]

                if event_type == "INPUT":
                    event_id = event["id"]

                    if event_id == "launch_viewer":
                        self._open_viewer()

                    elif event_id == "tick":
                        if self.viewer is not None and not self.viewer.is_running():
                            break

                        step_start = time.time()
                        self._step_ik(rate, event["metadata"])

                        if self.viewer is not None:
                            tick_viewer_window_fit(self.viewer)
                            self.viewer.sync()

                        time_until_next_step = self.model.opt.timestep - (
                            time.time() - step_start
                        )
                        if time_until_next_step > 0:
                            time.sleep(time_until_next_step)

                    elif event_id == "pull_position":
                        self.pull_position(self.node, event["metadata"])

                    elif event_id == "tick_ctrl":
                        if len(self.metadata) > 0:
                            self.node.send_output(
                                "mj_r_joints_pos", pa.array(self.motor_pos), self.metadata
                            )

                    elif event_id == "pull_velocity":
                        self.pull_velocity(self.node, event["metadata"])
                    elif event_id == "pull_current":
                        self.pull_current(self.node, event["metadata"])
                    elif event_id == "write_goal_position":
                        self.write_goal_position(event["value"])
                    elif event_id == "r_hand_pos":
                        self.write_mocap_pos(event["value"])
                    elif event_id == "r_hand_quat":
                        self.write_mocap_quat(event["value"])

                    elif event_id == "end":
                        break

                elif event_type == "ERROR":
                    raise ValueError(
                        "An error occurred in the dataflow: " + event["error"],
                    )
        finally:
            if self._viewer_ctx is not None:
                self._viewer_ctx.__exit__(None, None, None)

        self.node.send_output("end", pa.array([]))

    def pull_position(self, node, metadata):
        """TODO: Add docstring."""

    def pull_velocity(self, node, metadata):
        """TODO: Add docstring."""

    def pull_current(self, node, metadata):
        """TODO: Add docstring."""

    def write_goal_position(self, goal_position_with_joints):
        """TODO: Add docstring."""
        joints = goal_position_with_joints.field("joints")
        goal_position = goal_position_with_joints.field("values")

        for i, joint in enumerate(joints):
            self.data.joint(joint.as_py()).qpos[0] = goal_position[i].as_py()

    def write_mocap_pos(self, hand):

        #please, a method to access the mocap objects by name...

        if "r_tip1" in hand[0]:
            [x,y,z]=hand[0]['r_tip1'].values
            self.data.mocap_pos[0]=[x.as_py()*1.5-0.025,y.as_py()*1.5+0.022,z.as_py()*1.5+0.098]
        if "r_tip2" in hand[0]:
            [x,y,z]=hand[0]['r_tip2'].values
            self.data.mocap_pos[1]=[x.as_py()*1.5-0.025,y.as_py()*1.5-0.009,z.as_py()*1.5+0.092]
        if "r_tip3" in hand[0]:
            [x,y,z]=hand[0]['r_tip3'].values
            self.data.mocap_pos[2]=[x.as_py()*1.5-0.025,y.as_py()*1.5-0.040,z.as_py()*1.5+0.082]
        if "r_tip4" in hand[0]:
            [x,y,z]=hand[0]['r_tip4'].values
            self.data.mocap_pos[3]=[x.as_py()*1.5+0.024,y.as_py()*1.5+0.019,z.as_py()*1.5+0.017]


    def write_mocap_quat(self, hand):
        #please, a method to access the mocap objects by name...

        if "r_tip1" in hand[0]:
            [w,x,y,z]=hand[0]['r_tip1'].values
            self.data.mocap_quat[0]=[w.as_py(),x.as_py(),y.as_py(),z.as_py()]
        if "r_tip2" in hand[0]:
            [w,x,y,z]=hand[0]['r_tip2'].values
            self.data.mocap_quat[1]=[w.as_py(),x.as_py(),y.as_py(),z.as_py()]
        if "r_tip3" in hand[0]:
            [w,x,y,z]=hand[0]['r_tip3'].values
            self.data.mocap_quat[2]=[w.as_py(),x.as_py(),y.as_py(),z.as_py()]
        if "r_tip4" in hand[0]:
            [w,x,y,z]=hand[0]['r_tip4'].values
            self.data.mocap_quat[3]=[w.as_py(),x.as_py(),y.as_py(),z.as_py()]




def main():
    """Handle dynamic nodes, ask for the name of the node in the dataflow."""
    ensure_mjpython_on_macos()

    parser = argparse.ArgumentParser()
    parser.add_argument("-m", "--mode", type=str, choices=['pos','quat'], default='pos',
                    help="control mode: pos=position (we control the position of the tip) quat=quaternion (we control the orientation of the tip)")
    args = parser.parse_args()
    client = Client(args.mode)

    def close_viewer(*_args) -> None:
        if client._viewer_ctx is not None:
            try:
                client._viewer_ctx.__exit__(None, None, None)
            except Exception:
                pass
            client._viewer_ctx = None

    signal.signal(signal.SIGTERM, close_viewer)
    signal.signal(signal.SIGINT, close_viewer)

    client.run()


if __name__ == "__main__":
    main()
