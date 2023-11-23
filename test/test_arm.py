import numpy as np
from dm_control import mjcf
from numpy import testing

from dm_robotics.panda import arm, arm_constants, parameters


def test_physics_step():
  robot = arm.Panda()
  physics = mjcf.Physics.from_mjcf_model(robot.mjcf_model)
  physics.step()


def test_set_joint_angles():
  robot = arm.Panda()
  robot_params = parameters.RobotParams()
  physics = mjcf.Physics.from_mjcf_model(robot.mjcf_model)
  physics.step()
  robot.set_joint_angles(physics, robot_params.joint_positions)
  testing.assert_allclose(robot_params.joint_positions,
                          physics.bind(robot.joints).qpos)


def test_arm_effector():
  robot = arm.Panda()
  robot_params = parameters.RobotParams()
  physics = mjcf.Physics.from_mjcf_model(robot.mjcf_model)
  effector = arm.ArmEffector(robot_params, robot)
  effector.set_control(physics, np.zeros(7, dtype=np.float32))
  effector.close()


def test_arm_haptic():
  robot = arm.Panda()
  robot_params = parameters.RobotParams(
      actuation=arm_constants.Actuation.HAPTIC)
  physics = mjcf.Physics.from_mjcf_model(robot.mjcf_model)
  effector = arm.ArmEffector(robot_params, robot)
  effector.set_control(physics, np.zeros(0, dtype=np.float32))
