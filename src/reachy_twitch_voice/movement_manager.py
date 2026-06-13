from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from queue import Empty, Queue
from typing import Any, Protocol

from .types import BaselineMode, GesturePreset, IdleStyle

LOGGER = logging.getLogger(__name__)
CONTROL_LOOP_FREQUENCY_HZ = 100.0
Pose = Any
Antennas = tuple[float, float]


class MotionMove(Protocol):
    @property
    def duration(self) -> float:
        ...

    def evaluate(self, t: float) -> tuple[Pose | None, Antennas | None, float | None]:
        ...


FullBodyPose = tuple[Pose, Antennas, float]


def _matrix_copy(pose: Pose) -> Pose:
    if hasattr(pose, "copy"):
        try:
            return pose.copy()
        except Exception:
            pass
    return [list(row) for row in pose]


def _matrix_add(a: Pose, b: Pose, c: Pose | None = None) -> Pose:
    base = c or [[0.0 for _ in row] for row in a]
    out = []
    for i, row in enumerate(a):
        out_row = []
        for j, value in enumerate(row):
            out_row.append(float(value) + float(b[i][j]) - float(base[i][j]))
        out.append(out_row)
    return out


def _matrix_lerp(a: Pose, b: Pose, alpha: float) -> Pose:
    out = []
    for i, row in enumerate(a):
        out_row = []
        for j, value in enumerate(row):
            out_row.append(float(value) * (1.0 - alpha) + float(b[i][j]) * alpha)
        out.append(out_row)
    return out


def _fallback_head_pose(
    x: float,
    y: float,
    z: float,
    roll: float,
    pitch: float,
    yaw: float,
    *,
    degrees: bool = True,
    mm: bool = False,
) -> Pose:
    pose = [[0.0, 0.0, 0.0, 0.0] for _ in range(4)]
    for i in range(4):
        pose[i][i] = 1.0
    if mm:
        x /= 1000.0
        y /= 1000.0
        z /= 1000.0
    if degrees:
        roll = math.radians(roll)
        pitch = math.radians(pitch)
        yaw = math.radians(yaw)
    pose[0][3] = x
    pose[1][3] = y
    pose[2][3] = z
    pose[0][0] = yaw
    pose[1][1] = pitch
    pose[2][2] = roll
    return pose


def create_head_pose(
    x: float,
    y: float,
    z: float,
    roll: float,
    pitch: float,
    yaw: float,
    *,
    degrees: bool = True,
    mm: bool = False,
) -> Pose:
    try:
        from reachy_mini.utils import create_head_pose as sdk_create_head_pose  # type: ignore

        return sdk_create_head_pose(x, y, z, roll, pitch, yaw, degrees=degrees, mm=mm)
    except Exception:
        return _fallback_head_pose(x, y, z, roll, pitch, yaw, degrees=degrees, mm=mm)


def linear_pose_interpolation(left: Pose, right: Pose, alpha: float) -> Pose:
    try:
        from reachy_mini.utils.interpolation import linear_pose_interpolation as sdk_linear  # type: ignore

        return sdk_linear(left, right, alpha)
    except Exception:
        return _matrix_lerp(left, right, alpha)


def compose_world_offset(primary: Pose, secondary: Pose) -> Pose:
    try:
        from reachy_mini.utils.interpolation import compose_world_offset as sdk_compose  # type: ignore

        return sdk_compose(primary, secondary, reorthonormalize=True)
    except Exception:
        ident = _fallback_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        return _matrix_add(primary, secondary, ident)


def clone_full_body_pose(pose: FullBodyPose) -> FullBodyPose:
    head, antennas, body_yaw = pose
    return (_matrix_copy(head), (float(antennas[0]), float(antennas[1])), float(body_yaw))


class StaticPoseMove:
    def __init__(self, head: Pose, antennas: Antennas = (0.0, 0.0), body_yaw: float = 0.0) -> None:
        self.head = head
        self.antennas = antennas
        self.body_yaw = body_yaw

    @property
    def duration(self) -> float:
        return float("inf")

    def evaluate(self, t: float) -> tuple[Pose | None, Antennas | None, float | None]:
        return (_matrix_copy(self.head), self.antennas, self.body_yaw)


class BreathingBaselineMove:
    def __init__(self, neutral_head_pose: Pose) -> None:
        self.neutral_head_pose = neutral_head_pose

    @property
    def duration(self) -> float:
        return float("inf")

    def evaluate(self, t: float) -> tuple[Pose | None, Antennas | None, float | None]:
        # Primary breath: z-translation + pitch (slow, non-integer-ratio frequencies)
        z_offset = 0.003 * math.sin(2 * math.pi * 0.08 * t) + 0.0012 * math.sin(2 * math.pi * 0.19 * t)
        pitch = math.degrees(0.018 * math.sin(2 * math.pi * 0.11 * t) + 0.006 * math.sin(2 * math.pi * 0.23 * t))
        # Subtle micro-drift: very slow yaw and roll wander
        yaw = math.degrees(0.005 * math.sin(2 * math.pi * 0.031 * t + 1.1))
        roll = math.degrees(0.004 * math.sin(2 * math.pi * 0.027 * t + 0.7))
        head = linear_pose_interpolation(
            self.neutral_head_pose,
            create_head_pose(0, 0, z_offset, roll, pitch, yaw, degrees=True),
            1.0,
        )
        # Subtle antenna micro-drift (within max_idle_antenna limit of 0.22)
        ant_val = 0.008 * math.sin(2 * math.pi * 0.07 * t + 0.5)
        return (head, (ant_val, -ant_val), 0.0)


class LinearMove:
    def __init__(
        self,
        keyframes: list[tuple[float, Pose]],
        antenna_keyframes: list[tuple[float, Antennas]],
        duration: float,
    ) -> None:
        self.keyframes = sorted(keyframes, key=lambda x: x[0])
        self.antenna_keyframes = sorted(antenna_keyframes, key=lambda x: x[0])
        self._duration = duration

    @property
    def duration(self) -> float:
        return self._duration

    def evaluate(self, t: float) -> tuple[Pose | None, Antennas | None, float | None]:
        if t <= 0:
            pose = self.keyframes[0][1]
            antennas = self.antenna_keyframes[0][1]
        elif t >= self._duration:
            pose = self.keyframes[-1][1]
            antennas = self.antenna_keyframes[-1][1]
        else:
            r = t / self._duration
            left = self.keyframes[0]
            right = self.keyframes[-1]
            for i in range(1, len(self.keyframes)):
                if r <= self.keyframes[i][0]:
                    left = self.keyframes[i - 1]
                    right = self.keyframes[i]
                    break
            span = max(right[0] - left[0], 1e-6)
            alpha = (r - left[0]) / span
            pose = linear_pose_interpolation(left[1], right[1], alpha)

            a_left = self.antenna_keyframes[0]
            a_right = self.antenna_keyframes[-1]
            for i in range(1, len(self.antenna_keyframes)):
                if r <= self.antenna_keyframes[i][0]:
                    a_left = self.antenna_keyframes[i - 1]
                    a_right = self.antenna_keyframes[i]
                    break
            a_span = max(a_right[0] - a_left[0], 1e-6)
            a_alpha = (r - a_left[0]) / a_span
            antennas = (
                a_left[1][0] + (a_right[1][0] - a_left[1][0]) * a_alpha,
                a_left[1][1] + (a_right[1][1] - a_left[1][1]) * a_alpha,
            )
        return (_matrix_copy(pose), (float(antennas[0]), float(antennas[1])), 0.0)


def build_gesture_move(
    preset: GesturePreset,
    rng: random.Random,
    *,
    antenna_scale: float = 1.0,
    motion_scale: float = 1.0,
) -> MotionMove | None:
    center = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
    antenna_scale = min(max(antenna_scale, 0.0), 2.0)
    motion_scale = min(max(motion_scale, 0.2), 2.0)
    if preset in {"nod", "look", "sway", "tilt"}:
        antenna_scale *= 0.25
        motion_scale *= 1.10
        antenna_limit = 0.28
    elif preset in {"idle"}:
        antenna_limit = 0.10
    else:
        antenna_limit = 0.18

    def a(v: float) -> float:
        raw = v * antenna_scale
        return max(min(raw, antenna_limit), -antenna_limit)

    def m(v: float) -> float:
        return v * motion_scale

    antenna_jitter = rng.uniform(-0.35, 0.35)
    if preset == "nod":
        down_pitch = m(10 + rng.uniform(1.5, 5.0))
        up_pitch = m(-(5 + rng.uniform(1.0, 4.0)))
        down = create_head_pose(0, 0, 0, 0, down_pitch, 0, degrees=True)
        up = create_head_pose(0, 0, 0, 0, up_pitch, 0, degrees=True)
        fwd = 1.8 + antenna_jitter
        back = -0.8 + antenna_jitter
        return LinearMove(
            [(0.0, center), (0.45, down), (0.75, up), (1.0, center)],
            [(0.0, (0.0, 0.0)), (0.45, (a(fwd), a(fwd))), (0.75, (a(back), a(back))), (1.0, (0.0, 0.0))],
            0.82 + rng.uniform(0.08, 0.18),
        )
    if preset == "look":
        yaw = m(12 + rng.uniform(2.0, 7.0))
        left = create_head_pose(0, 0, 0, 0, 0, yaw, degrees=True)
        right = create_head_pose(0, 0, 0, 0, 0, -yaw, degrees=True)
        split = 1.6 + antenna_jitter
        return LinearMove(
            [(0.0, center), (0.42, left), (0.78, right), (1.0, center)],
            [(0.0, (0.0, 0.0)), (0.42, (a(split), a(-split))), (0.78, (a(-split), a(split))), (1.0, (0.0, 0.0))],
            0.95 + rng.uniform(0.05, 0.20),
        )
    if preset == "sway":
        yaw = m(8 + rng.uniform(2.0, 5.0))
        roll = m(4 + rng.uniform(0.0, 3.0))
        left = create_head_pose(0, 0, 0, -roll, 0, yaw, degrees=True)
        right = create_head_pose(0, 0, 0, roll, 0, -yaw, degrees=True)
        flow = 2.0 + antenna_jitter
        return LinearMove(
            [(0.0, center), (0.33, left), (0.66, right), (1.0, center)],
            [(0.0, (0.0, 0.0)), (0.33, (a(-flow), a(flow))), (0.66, (a(flow), a(-flow))), (1.0, (0.0, 0.0))],
            1.05 + rng.uniform(0.08, 0.20),
        )
    if preset == "tilt":
        roll = m(7 + rng.uniform(1.0, 5.0))
        yaw = m(3 + rng.uniform(0.5, 3.0))
        left_tilt = create_head_pose(0, 0, 0, -roll, 0, yaw, degrees=True)
        right_tilt = create_head_pose(0, 0, 0, roll, 0, -yaw, degrees=True)
        accent = 1.8 + antenna_jitter
        return LinearMove(
            [(0.0, center), (0.4, left_tilt), (0.8, right_tilt), (1.0, center)],
            [(0.0, (0.0, 0.0)), (0.4, (a(-accent), a(-accent * 0.35))), (0.8, (a(accent * 0.35), a(accent))), (1.0, (0.0, 0.0))],
            0.88 + rng.uniform(0.05, 0.16),
        )
    if preset == "idle":
        attentive = create_head_pose(0, 0, 0, 0, 3.0, 0, degrees=True)
        return LinearMove([(0.0, center), (0.5, attentive), (1.0, center)], [(0.0, (0.0, 0.0)), (1.0, (0.0, 0.0))], 1.6)
    if preset == "blink":
        # Short antenna flick — "blink" like reflex, ~0.28s
        peak = 0.14 + rng.uniform(0.0, 0.06)
        jitter = rng.choice([-1, 1])
        return LinearMove(
            [(0.0, center), (0.5, center), (1.0, center)],
            [
                (0.0, (0.0, 0.0)),
                (0.4, (a(peak * jitter), a(-peak * jitter))),
                (1.0, (0.0, 0.0)),
            ],
            0.28 + rng.uniform(0.0, 0.08),
        )
    return None


@dataclass
class MovementState:
    one_shot_move: MotionMove | None = None
    one_shot_start_time: float | None = None
    last_activity_time: float = 0.0
    speech_offsets: tuple[float, float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    face_tracking_offsets: tuple[float, float, float, float, float, float] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    last_primary_pose: FullBodyPose | None = None
    baseline_mode: BaselineMode = "attentive_idle"

    def update_activity(self) -> None:
        self.last_activity_time = time.monotonic()


class MovementManager:
    def __init__(
        self,
        current_robot: Any,
        *,
        idle_inactivity_delay: float = 3.0,
        target_frequency: float = CONTROL_LOOP_FREQUENCY_HZ,
        idle_style: IdleStyle = "attentive",
        idle_first_delay: float = 3.0,
        idle_glance_interval: float = 10.0,
        rng: random.Random | None = None,
    ) -> None:
        self.current_robot = current_robot
        self.idle_inactivity_delay = idle_inactivity_delay
        self.target_frequency = target_frequency
        self.target_period = 1.0 / target_frequency
        self.idle_style = idle_style
        self.idle_first_delay = max(0.0, idle_first_delay)
        self.idle_glance_interval = max(1.0, idle_glance_interval)
        self._rng = rng or random.Random(time.time_ns())
        self._now = time.monotonic
        self.state = MovementState(last_activity_time=self._now())
        self._neutral_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self._attentive_pose = create_head_pose(0, 0, 0, 0, 3.0 if idle_style == "attentive" else 1.5, 0, degrees=True)
        self._breathing_move = BreathingBaselineMove(self._attentive_pose)
        self.state.last_primary_pose = (self._neutral_pose, (0.0, 0.0), 0.0)
        self._idle_phrase_candidates: list[GesturePreset] = ["nod", "look"]
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._command_queue: Queue[tuple[str, Any]] = Queue()
        self._speech_offsets_lock = threading.Lock()
        self._pending_speech_offsets = self.state.speech_offsets
        self._speech_offsets_dirty = False
        self._face_offsets_lock = threading.Lock()
        self._pending_face_offsets = self.state.face_tracking_offsets
        self._face_offsets_dirty = False
        self._shared_state_lock = threading.Lock()
        self._shared_last_activity_time = self.state.last_activity_time
        self._shared_is_speaking = False
        self._is_speaking = False
        self._last_idle_phrase_at = self._now()
        self._last_commanded_pose = clone_full_body_pose(self.state.last_primary_pose)
        self._last_command_error = 0.0
        self._command_error_interval = 1.0
        self._max_speaking_antenna = 0.12
        self._max_idle_antenna = 0.22
        # A-3a: baseline crossfade smoothing
        self._smoothed_baseline_pose: FullBodyPose | None = None
        self._baseline_smooth_alpha: float = 0.06  # per tick at 100Hz → ~0.6s settling time
        # A-3b: speech offset release ramp
        self._offset_smooth_alpha: float = 0.12   # faster than baseline (fades in ~0.5s at 100Hz)
        self._speech_offsets_target: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self._speech_antenna_target: tuple[float, float] = (0.0, 0.0)
        self._applied_speech_offsets: tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self._applied_speech_antenna: tuple[float, float] = (0.0, 0.0)
        # A-1c: random blink trigger
        self._last_blink_at: float = self._now()
        self._next_blink_interval: float = rng.uniform(4.0, 9.0) if rng else 6.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self.working_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=2.0)
            self._thread = None
        self._reset_to_neutral()

    def queue_move(self, move: MotionMove) -> None:
        self._command_queue.put(("queue_move", move))

    def clear_move_queue(self) -> None:
        self._command_queue.put(("clear_queue", None))

    def mark_activity(self) -> None:
        self._command_queue.put(("mark_activity", None))

    def set_idle_phrase_candidates(self, candidates: list[GesturePreset]) -> None:
        self._command_queue.put(("idle_candidates", candidates))

    def set_speech_offsets(self, offsets: tuple[float, float, float, float, float, float]) -> None:
        with self._speech_offsets_lock:
            self._pending_speech_offsets = offsets
            self._speech_offsets_dirty = True

    def set_speech_antenna_offsets(self, ant: tuple[float, float]) -> None:
        """Set antenna target for mouth-sync; smoothed toward each tick."""
        self._speech_antenna_target = (float(ant[0]), float(ant[1]))

    def set_face_tracking_offsets(self, offsets: tuple[float, float, float, float, float, float]) -> None:
        with self._face_offsets_lock:
            self._pending_face_offsets = offsets
            self._face_offsets_dirty = True

    def set_listening(self, listening: bool) -> None:
        self.set_speaking(listening)

    def set_speaking(self, speaking: bool) -> None:
        self._command_queue.put(("set_speaking", bool(speaking)))

    def is_idle(self) -> bool:
        with self._shared_state_lock:
            last_activity = self._shared_last_activity_time
            speaking = self._shared_is_speaking
        return not speaking and (self._now() - last_activity >= self.idle_inactivity_delay)

    def _apply_pending_offsets(self) -> None:
        speech_offsets = None
        with self._speech_offsets_lock:
            if self._speech_offsets_dirty:
                speech_offsets = self._pending_speech_offsets
                self._speech_offsets_dirty = False
        if speech_offsets is not None:
            # A-3b: update target instead of applying directly; _smooth_offsets() converges each tick
            self._speech_offsets_target = speech_offsets
            self.state.update_activity()
        face_offsets = None
        with self._face_offsets_lock:
            if self._face_offsets_dirty:
                face_offsets = self._pending_face_offsets
                self._face_offsets_dirty = False
        if face_offsets is not None:
            self.state.face_tracking_offsets = face_offsets
            self.state.update_activity()

    def _handle_command(self, command: str, payload: Any) -> None:
        if command == "queue_move":
            self.state.one_shot_move = payload
            self.state.one_shot_start_time = self._now()
            self.state.update_activity()
        elif command == "clear_queue":
            self.state.one_shot_move = None
            self.state.one_shot_start_time = None
        elif command == "mark_activity":
            self.state.update_activity()
        elif command == "set_speaking":
            self._is_speaking = bool(payload)
            self.state.update_activity()
        elif command == "idle_candidates":
            candidates = [c for c in payload if c in {"nod", "look", "sway", "tilt", "idle", "blink"}]
            self._idle_phrase_candidates = candidates or ["nod", "look"]

    def _poll_signals(self) -> None:
        self._apply_pending_offsets()
        while True:
            try:
                command, payload = self._command_queue.get_nowait()
            except Empty:
                break
            self._handle_command(command, payload)

    def _manage_one_shot(self, current_time: float) -> None:
        if self.state.one_shot_move is None or self.state.one_shot_start_time is None:
            return
        if current_time - self.state.one_shot_start_time >= self.state.one_shot_move.duration:
            self.state.one_shot_move = None
            self.state.one_shot_start_time = None

    def _current_robot_pose(self) -> FullBodyPose:
        try:
            current_head_pose = self.current_robot.get_current_head_pose()
        except Exception:
            current_head_pose = self._last_commanded_pose[0]
        try:
            _, current_antennas = self.current_robot.get_current_joint_positions()
            antennas = (float(current_antennas[0]), float(current_antennas[1]))
        except Exception:
            antennas = self._last_commanded_pose[1]
        return (current_head_pose, antennas, 0.0)

    def _select_baseline_mode(self, current_time: float) -> BaselineMode:
        if self._is_speaking:
            return "attentive_idle"
        inactive_for = current_time - self.state.last_activity_time
        if inactive_for < self.idle_first_delay:
            return "attentive_idle"
        if inactive_for < self.idle_first_delay + 7.0:
            return "breathing_idle"
        return "breathing_idle"

    def _maybe_trigger_idle_phrase(self, current_time: float) -> None:
        if self._is_speaking or self.state.one_shot_move is not None:
            return
        inactive_for = current_time - self.state.last_activity_time
        if inactive_for < self.idle_first_delay + 7.0:
            return
        if current_time - self._last_idle_phrase_at < self.idle_glance_interval:
            return
        preset = self._rng.choice(self._idle_phrase_candidates)
        move = build_gesture_move(preset, self._rng, antenna_scale=0.7, motion_scale=0.75)
        if move is not None:
            self.state.one_shot_move = move
            self.state.one_shot_start_time = current_time
            self._last_idle_phrase_at = current_time

    def _get_baseline_pose(self, current_time: float) -> FullBodyPose:
        baseline_mode = self._select_baseline_mode(current_time)
        self.state.baseline_mode = baseline_mode
        if baseline_mode == "neutral":
            raw = (self._neutral_pose, (0.0, 0.0), 0.0)
        elif baseline_mode == "attentive_idle":
            raw = (self._attentive_pose, (0.0, 0.0), 0.0)
        else:
            raw = self._breathing_move.evaluate(current_time)
        t_head, t_ant_raw, t_yaw_raw = raw
        t_ant = (float(t_ant_raw[0]) if t_ant_raw else 0.0, float(t_ant_raw[1]) if t_ant_raw else 0.0)
        t_yaw = float(t_yaw_raw) if t_yaw_raw is not None else 0.0
        # A-3a: exponential crossfade so baseline switches are smooth
        if self._smoothed_baseline_pose is None:
            self._smoothed_baseline_pose = (_matrix_copy(t_head), t_ant, t_yaw)
            return clone_full_body_pose(self._smoothed_baseline_pose)
        s_head, s_ant, s_yaw = self._smoothed_baseline_pose
        alpha = self._baseline_smooth_alpha
        new_head = linear_pose_interpolation(s_head, t_head, alpha)
        new_ant = (
            s_ant[0] + (t_ant[0] - s_ant[0]) * alpha,
            s_ant[1] + (t_ant[1] - s_ant[1]) * alpha,
        )
        new_yaw = s_yaw + (t_yaw - s_yaw) * alpha
        self._smoothed_baseline_pose = (new_head, new_ant, new_yaw)
        return clone_full_body_pose(self._smoothed_baseline_pose)

    def _get_primary_pose(self, current_time: float) -> FullBodyPose:
        base_pose = self._get_baseline_pose(current_time)
        if self.state.one_shot_move is not None and self.state.one_shot_start_time is not None:
            move_time = current_time - self.state.one_shot_start_time
            head, antennas, body_yaw = self.state.one_shot_move.evaluate(move_time)
            if head is None:
                head = base_pose[0]
            if antennas is None:
                antennas = base_pose[1]
            if body_yaw is None:
                body_yaw = base_pose[2]
            pose = (_matrix_copy(head), (float(antennas[0]), float(antennas[1])), float(body_yaw))
            self.state.last_primary_pose = clone_full_body_pose(pose)
            return pose
        self.state.last_primary_pose = clone_full_body_pose(base_pose)
        return base_pose

    def _get_secondary_pose(self) -> FullBodyPose:
        offsets = [self._applied_speech_offsets[i] + self.state.face_tracking_offsets[i] for i in range(6)]
        secondary_head = create_head_pose(offsets[0], offsets[1], offsets[2], offsets[3], offsets[4], offsets[5], degrees=False, mm=False)
        return (secondary_head, (0.0, 0.0), 0.0)

    def _smooth_offsets(self) -> None:
        """A-3b: Converge applied speech offsets toward targets each control tick."""
        alpha = self._offset_smooth_alpha
        so = self._applied_speech_offsets
        st = self._speech_offsets_target
        self._applied_speech_offsets = tuple(
            so[i] + (st[i] - so[i]) * alpha for i in range(6)
        )
        sa = self._applied_speech_antenna
        at = self._speech_antenna_target
        self._applied_speech_antenna = (
            sa[0] + (at[0] - sa[0]) * alpha,
            sa[1] + (at[1] - sa[1]) * alpha,
        )
        self.state.speech_offsets = self._applied_speech_offsets  # type: ignore[assignment]

    def _maybe_trigger_idle_blink(self, current_time: float) -> None:
        """A-1c: Trigger a random antenna blink/twitch gesture during idle."""
        if self._is_speaking or self.state.one_shot_move is not None:
            return
        inactive_for = current_time - self.state.last_activity_time
        if inactive_for < self.idle_first_delay:
            return
        if current_time - self._last_blink_at < self._next_blink_interval:
            return
        move = build_gesture_move("blink", self._rng)
        if move is not None:
            self.state.one_shot_move = move
            self.state.one_shot_start_time = current_time
            self._last_blink_at = current_time
            self._next_blink_interval = self._rng.uniform(4.0, 9.0)

    def _compose_pose(self, current_time: float) -> FullBodyPose:
        self._maybe_trigger_idle_phrase(current_time)
        self._maybe_trigger_idle_blink(current_time)
        primary_head, primary_antennas, primary_body_yaw = self._get_primary_pose(current_time)
        secondary_head, _, secondary_body_yaw = self._get_secondary_pose()
        combined_head = compose_world_offset(primary_head, secondary_head)
        # A-3b: add speech antenna offset (mouth-sync channel) to primary antennas
        speech_ant = self._applied_speech_antenna
        combined_antennas = (
            primary_antennas[0] + speech_ant[0],
            primary_antennas[1] + speech_ant[1],
        )
        return (combined_head, combined_antennas, primary_body_yaw + secondary_body_yaw)

    def _clamp_antennas(self, target_antennas: Antennas) -> Antennas:
        limit = self._max_speaking_antenna if self._is_speaking else self._max_idle_antenna
        return (
            max(-limit, min(limit, float(target_antennas[0]))),
            max(-limit, min(limit, float(target_antennas[1]))),
        )

    def _issue_control_command(self, head: Pose, antennas: Antennas, body_yaw: float) -> None:
        try:
            if hasattr(self.current_robot, "set_target"):
                self.current_robot.set_target(head=head, antennas=antennas, body_yaw=body_yaw)
            else:
                if hasattr(self.current_robot, "set_target_head_pose"):
                    self.current_robot.set_target_head_pose(head)
                if hasattr(self.current_robot, "set_target_antenna_joint_positions"):
                    self.current_robot.set_target_antenna_joint_positions([antennas[0], antennas[1]])
        except Exception as exc:
            now = self._now()
            if now - self._last_command_error >= self._command_error_interval:
                LOGGER.warning("Failed to set robot target: %s", exc)
                self._last_command_error = now
            return
        self._last_commanded_pose = clone_full_body_pose((head, antennas, body_yaw))

    def _publish_shared_state(self) -> None:
        with self._shared_state_lock:
            self._shared_last_activity_time = self.state.last_activity_time
            self._shared_is_speaking = self._is_speaking

    def _reset_to_neutral(self) -> None:
        try:
            if hasattr(self.current_robot, "goto_target"):
                self.current_robot.goto_target(head=self._neutral_pose, antennas=[0.0, 0.0], duration=1.0, body_yaw=0.0)
            else:
                self._issue_control_command(self._neutral_pose, (0.0, 0.0), 0.0)
        except Exception:
            pass

    def working_loop(self) -> None:
        while not self._stop_event.is_set():
            loop_start = self._now()
            self._poll_signals()
            self._smooth_offsets()
            self._manage_one_shot(loop_start)
            head, antennas, body_yaw = self._compose_pose(loop_start)
            self._issue_control_command(head, self._clamp_antennas(antennas), body_yaw)
            self._publish_shared_state()
            sleep_time = max(0.0, self.target_period - (self._now() - loop_start))
            if sleep_time > 0:
                time.sleep(sleep_time)


__all__ = ["BreathingBaselineMove", "MovementManager", "MotionMove", "build_gesture_move", "clone_full_body_pose", "create_head_pose"]
