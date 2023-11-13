"""Utility module for the Panda MoMa model."""
import argparse
import enum
import logging
import pickle
import threading
from collections import deque
from typing import Dict, Hashable, Optional, Sequence, Tuple

import mujoco
import numpy as np
from dm_control import mjcf
from dm_control.composer.observation import observable
from dm_control.rl import control
from dm_control.viewer import application, renderer
from dm_control.viewer import runtime as runtime_module
from dm_control.viewer import user_input, views
from dm_robotics.geometry import geometry
from dm_robotics.moma import robot, sensor
from dm_robotics.transformations import transformations as tr


def full_spec(env: control.Environment):
  """ Prints the full specification of the environment, i.e.
  action, observation and reward spec."""
  print(f'Action spec: {env.action_spec()}')
  print('Observation spec:')
  obs_spec = env.observation_spec()
  for key, spec in obs_spec.items():
    print(f'\t{key}: {spec}')
  print(f'Reward spec: {env.reward_spec()}')


def default_arg_parser(desc: str = 'dm_robotics_panda',
                       dual_arm: bool = False) -> argparse.ArgumentParser:
  """Create an ArgumentParser with default parameters.
  Args:
    desc: Description shown in the help screen.
    dual_arm: Create parameters for a dual-arm setup."""

  def add_hil_args(parser: argparse.ArgumentParser, prefix: str = ''):
    parser.add_argument(f'--{prefix}robot-ip',
                        type=str,
                        default=None,
                        help='Robot IP for hardware in the loop.')

  parser = argparse.ArgumentParser(description=desc)
  parser.add_argument('-g', '--gui', action='store_true')
  if not dual_arm:
    add_hil_args(parser)
  else:
    add_hil_args(parser, prefix='left-')
    add_hil_args(parser, prefix='right-')
  return parser


class Formatter(logging.Formatter):
  """ Logging formatter for the Panda MoMa model. """

  def format(self, record: logging.LogRecord) -> str:
    msg = ''
    if record.levelno == logging.WARNING:
      msg = '\033[33m'
    elif record.levelno == logging.ERROR:
      msg = '\033[31m'
    msg += super().format(record) + '\033[0m'
    return msg


class DuplicateFilter(logging.Filter):

  def __init__(self, dead_time: float = 5.0) -> None:
    self._dead_time = dead_time

  def filter(self, record: logging.LogRecord):
    # add other fields if you need more granular comparison, depends on your app
    current_log = (record.module, record.levelno, record.msg)
    if current_log != getattr(self, '_last_log',
                              None) or record.created - getattr(
                                  self, '_last_created', 0) > self._dead_time:
      self._last_log = current_log
      self._last_created = record.created
      return True
    return False


def init_logging() -> None:
  """ Set the standard log format and handler. """
  for h in logging.root.handlers[:]:
    logging.root.removeHandler(h)
    h.close()
  handler = logging.StreamHandler()
  handler.setFormatter(
      Formatter('[%(asctime)s][%(name)s] %(message)s', '%Y-%m-%d %H:%M:%S'))
  handler.addFilter(DuplicateFilter())
  logging.root.setLevel(logging.INFO)
  logging.root.addHandler(handler)
  logging.captureWarnings(True)


def set_joint_stiffness(stiffness: Sequence[float], arm: robot.Arm,
                        physics: mjcf.Physics):
  """ Update the joint actuation stiffness of the robot arm. """
  physics_actuators = physics.bind(arm.actuators)
  physics_actuators.gainprm[:, 0] = stiffness
  physics_actuators.biasprm[:, 1] = -np.array(stiffness)


def set_joint_damping(damping: Sequence[float], arm: robot.Arm,
                      physics: mjcf.Physics):
  """ Update the joint actuation damping of the robot arm. """
  physics_actuators = physics.bind(arm.actuators)
  physics_actuators.biasprm[:, 2] = -np.array(damping)


@enum.unique
class TimeObservation(enum.Enum):
  TIME = 'time'

  def get_obs_key(self, name: str) -> str:
    """Returns the key to the observation in the observables dict."""
    return self.value.format(name)


class TimeSensor(sensor.Sensor):
  """ MoMa sensor measuring simulation time. """

  def __init__(self) -> None:
    self._observables = {
        self.get_obs_key(TimeObservation.TIME): observable.Generic(self._time)
    }
    for obs in self._observables.values():
      obs.enabled = True

  def initialize_episode(self, physics: mjcf.Physics,
                         random_state: np.random.RandomState) -> None:
    pass

  @property
  def name(self) -> str:
    return 'time'

  @property
  def observables(self) -> Dict[str, observable.Observable]:
    return self._observables

  def get_obs_key(self, obs: enum.Enum) -> str:
    return obs.get_obs_key(self.name)

  def _time(self, physics: mjcf.Physics) -> np.ndarray:
    return np.array([physics.data.time])


class PlotComponent(renderer.Component):
  """ A plotting component for `dm_control.viewer.application.Application`. """

  def __init__(self,
               runtime: runtime_module.Runtime,
               maxlen: int = 500) -> None:
    self._rt = runtime
    self._obs_idx = None
    self._obs_keys = None
    self.maxlen = min(maxlen, mujoco.mjMAXLINEPNT)
    self.maxlines = 0
    self.x = np.linspace(-self.maxlen, 0, self.maxlen)
    self.y = []
    self.fig = mujoco.MjvFigure()
    self.fig.figurergba = (0, 0, 0, 0.5)
    self.fig.flg_barplot = 0
    self.fig.flg_selection = 0
    self.fig.range = [[1, 0], [1, 0]]

  def _init_buffer(self):
    for obs in self._rt._time_step.observation.values():
      lines = np.atleast_1d(obs).shape[0]
      if lines > self.maxlines:
        self.maxlines = lines
    for _1 in range(self.maxlines):
      self.y.append(deque(maxlen=self.maxlen))
    self.reset_data()
    self._obs_idx = 0
    self._obs_keys = list(self._rt._time_step.observation.keys())

  def render(self, context, viewport):
    if self._rt._time_step is None:
      return
    if self._obs_idx is None:
      self._init_buffer()
    pos = mujoco.MjrRect(5, viewport.height - 256 - 5, 256, 256)
    obs = np.atleast_1d(
        self._rt._time_step.observation[self._obs_keys[self._obs_idx]])
    for i in range(self.maxlines):
      if i < obs.shape[0]:
        self.fig.linepnt[i] = self.maxlen
        self.y[i].append(obs[i])
        self.fig.linedata[i][:self.maxlen * 2] = np.array([self.x, self.y[i]
                                                          ]).T.reshape((-1,))
      else:
        self.fig.linepnt[i] = 0
    self.fig.title = f'{self._obs_keys[self._obs_idx]:100s}'
    mujoco.mjr_figure(pos, self.fig, context.ptr)

  def reset_data(self):
    for i in range(self.maxlines):
      for j in range(self.maxlen):
        del j
        self.y[i].append(0)

  def next_obs(self):
    self._obs_idx = (self._obs_idx + 1) % len(self._obs_keys)
    self.reset_data()

  def prev_obs(self):
    self._obs_idx = (self._obs_idx - 1) % len(self._obs_keys)
    self.reset_data()


class PlotHelp(views.ColumnTextModel):

  def __init__(self) -> None:
    self._value = [['Plot', ''], ['Next observation', 'F4'],
                   ['Previous observation', 'F3']]

  def get_columns(self):
    return self._value


class ApplicationWithPlot(application.Application):

  def __init__(self, title='Explorer', width=1024, height=768):
    super().__init__(title, width, height)
    self._pause_subject.value = False
    self._viewer_layout.add(views.ColumnTextView(PlotHelp()),
                            views.PanelLocation.BOTTOM_RIGHT)

  def _perform_deferred_reload(self, params):
    super()._perform_deferred_reload(params)
    cmp = PlotComponent(self._runtime)
    self._renderer.components += cmp
    self._input_map.bind(cmp.next_obs, user_input.KEY_F4)
    self._input_map.bind(cmp.prev_obs, user_input.KEY_F3)
