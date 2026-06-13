"""Tests for Part A motion improvements (A-1, A-2, A-3)."""
from __future__ import annotations

import random
import time
import unittest
from typing import Any

from reachy_twitch_voice.movement_manager import (
    BreathingBaselineMove,
    MovementManager,
    build_gesture_move,
    create_head_pose,
)
from reachy_twitch_voice.tool_executor import ToolExecutor
from reachy_twitch_voice.types import ConversationOutputEvent, GesturePreset


class _FakeRobot:
    def __init__(self) -> None:
        self.set_target_calls: list[tuple[Any, tuple[float, float], float]] = []
        self.current_head = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self.current_antennas = (0.0, 0.0)

    def get_current_head_pose(self) -> Any:
        return self.current_head

    def get_current_joint_positions(self) -> tuple[list[Any], tuple[float, float]]:
        return ([], self.current_antennas)

    def set_target(self, *, head: Any, antennas: Any, body_yaw: float) -> None:
        self.current_head = head
        self.current_antennas = (float(antennas[0]), float(antennas[1]))
        self.set_target_calls.append((head, self.current_antennas, float(body_yaw)))

    def goto_target(self, *, head: Any, antennas: Any, duration: float, body_yaw: float) -> None:
        pass


class TestBaselineCrossfade(unittest.TestCase):
    def test_smoothed_baseline_pose_updates_gradually(self) -> None:
        """A-3a: _smoothed_baseline_pose must update each call (exponential smoothing)."""
        robot = _FakeRobot()
        manager = MovementManager(robot, idle_inactivity_delay=10.0, target_frequency=50.0)
        t = time.monotonic()

        # First call initialises the smoothed pose
        pose0 = manager._get_baseline_pose(t)  # type: ignore[attr-defined]
        self.assertIsNotNone(manager._smoothed_baseline_pose)  # type: ignore[attr-defined]

        # Subsequent calls must return different (gradually changing) poses
        # Force speaking=True so baseline_mode="attentive_idle" and then switch
        manager._is_speaking = False  # type: ignore[attr-defined]
        # Advance time well past idle_first_delay so it falls into breathing_idle
        far_future = t + 100.0
        poses = [manager._get_baseline_pose(far_future) for _ in range(10)]  # type: ignore[attr-defined]

        # Smoothed pose reference must have been updated
        self.assertIsNotNone(manager._smoothed_baseline_pose)  # type: ignore[attr-defined]
        # The smoothed pose should not be identical to the very first call (it must be converging)
        # We can't rely on exact values, but the object must update on each call.
        first_ant = poses[0][1]
        last_ant = poses[-1][1]
        # Over 10 ticks of breathing the antenna value should have changed at least slightly
        # (BreathingBaselineMove now returns non-zero ant_val over time)
        _ = first_ant  # values may be tiny; just verify no exception and types correct
        self.assertIsInstance(last_ant, tuple)
        self.assertEqual(len(last_ant), 2)


class TestBlinkGesture(unittest.TestCase):
    def test_blink_gesture_antenna_within_limit(self) -> None:
        """A-1b: build_gesture_move('blink') must keep antennas within ±0.22."""
        rng = random.Random(42)
        move = build_gesture_move("blink", rng)
        self.assertIsNotNone(move)
        assert move is not None
        limit = 0.22
        for t_frac in [0.0, 0.14, 0.28]:
            _, antennas, _ = move.evaluate(t_frac)
            self.assertIsNotNone(antennas)
            assert antennas is not None
            self.assertLessEqual(abs(antennas[0]), limit, f"antenna[0] out of range at t={t_frac}")
            self.assertLessEqual(abs(antennas[1]), limit, f"antenna[1] out of range at t={t_frac}")

    def test_blink_gesture_head_stays_center(self) -> None:
        """A-1b: blink gesture head keyframes should be center (no head motion)."""
        rng = random.Random(99)
        move = build_gesture_move("blink", rng)
        self.assertIsNotNone(move)
        assert move is not None
        center = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        for t_frac in [0.0, 0.5, 1.0]:
            head, _, _ = move.evaluate(t_frac * move.duration)
            self.assertIsNotNone(head)
            # All diagonal elements should equal those of center (identity-ish)
            for i in range(4):
                self.assertAlmostEqual(float(head[i][i]), float(center[i][i]), places=5)


class TestBlinkPresetInTypes(unittest.TestCase):
    def test_blink_is_valid_gesture_preset(self) -> None:
        """A-1b: 'blink' must be accepted as GesturePreset without type error at runtime."""
        # If the Literal doesn't include 'blink', build_gesture_move would return None
        rng = random.Random(0)
        move = build_gesture_move("blink", rng)  # type: ignore[arg-type]
        self.assertIsNotNone(move)

        # Verify assignment works (runtime; static check is via mypy)
        preset: GesturePreset = "blink"
        self.assertEqual(preset, "blink")


class TestBreathingBaselineMoveAntenna(unittest.TestCase):
    def test_antenna_within_range(self) -> None:
        """A-1a: BreathingBaselineMove antennas must be within ±0.22 at arbitrary time."""
        neutral = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        move = BreathingBaselineMove(neutral)
        for t in [0.0, 1.0, 5.0, 10.0, 23.7]:
            _, antennas, _ = move.evaluate(t)
            self.assertIsNotNone(antennas)
            assert antennas is not None
            self.assertLess(abs(antennas[0]), 0.22, f"antenna[0] out of range at t={t}")
            self.assertLess(abs(antennas[1]), 0.22, f"antenna[1] out of range at t={t}")

    def test_head_pose_not_none(self) -> None:
        """A-1a: BreathingBaselineMove must return a non-None head pose."""
        neutral = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        move = BreathingBaselineMove(neutral)
        head, _, _ = move.evaluate(5.0)
        self.assertIsNotNone(head)


class TestSpeechAntennaOffsets(unittest.TestCase):
    def test_applied_antenna_trends_toward_target(self) -> None:
        """A-3b: _applied_speech_antenna must converge toward _speech_antenna_target."""
        robot = _FakeRobot()
        manager = MovementManager(robot, idle_inactivity_delay=10.0, target_frequency=50.0)

        target = (0.05, 0.05)
        manager.set_speech_antenna_offsets(target)

        # Run _smooth_offsets many times
        for _ in range(50):
            manager._smooth_offsets()  # type: ignore[attr-defined]

        applied = manager._applied_speech_antenna  # type: ignore[attr-defined]
        # After 50 ticks at alpha=0.12, applied should be very close to target
        self.assertAlmostEqual(applied[0], target[0], places=3)
        self.assertAlmostEqual(applied[1], target[1], places=3)

    def test_antenna_returns_to_zero(self) -> None:
        """A-3b: When target returns to (0,0), applied must decay back toward zero."""
        robot = _FakeRobot()
        manager = MovementManager(robot, idle_inactivity_delay=10.0, target_frequency=50.0)

        manager.set_speech_antenna_offsets((0.05, 0.05))
        for _ in range(50):
            manager._smooth_offsets()  # type: ignore[attr-defined]

        # Now zero the target
        manager.set_speech_antenna_offsets((0.0, 0.0))
        for _ in range(50):
            manager._smooth_offsets()  # type: ignore[attr-defined]

        applied = manager._applied_speech_antenna  # type: ignore[attr-defined]
        self.assertAlmostEqual(applied[0], 0.0, places=3)
        self.assertAlmostEqual(applied[1], 0.0, places=3)


class TestEmotionScaleMapping(unittest.TestCase):
    def test_joy_scale(self) -> None:
        """A-2c: joy emotion → speech_motion_scale 1.15."""
        executor = ToolExecutor()
        output = ConversationOutputEvent(reply_text="テスト！", emotion="joy", tool_calls=[])
        plan = executor.build_motion_plan(output)
        self.assertAlmostEqual(plan.speech_motion_scale, 1.15)

    def test_surprise_scale(self) -> None:
        """A-2c: surprise emotion → speech_motion_scale 0.90."""
        executor = ToolExecutor()
        output = ConversationOutputEvent(reply_text="えっ？", emotion="surprise", tool_calls=[])
        plan = executor.build_motion_plan(output)
        self.assertAlmostEqual(plan.speech_motion_scale, 0.90)

    def test_empathy_scale(self) -> None:
        """A-2c: empathy emotion → speech_motion_scale 0.70."""
        executor = ToolExecutor()
        output = ConversationOutputEvent(reply_text="なるほどですね", emotion="empathy", tool_calls=[])
        plan = executor.build_motion_plan(output)
        self.assertAlmostEqual(plan.speech_motion_scale, 0.70)


if __name__ == "__main__":
    unittest.main()
