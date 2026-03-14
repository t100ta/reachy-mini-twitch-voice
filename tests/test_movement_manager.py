import time
import unittest
from typing import Any

from reachy_twitch_voice.movement_manager import MovementManager, build_gesture_move, create_head_pose


class _FakeRobot:
    def __init__(self) -> None:
        self.set_target_calls: list[tuple[Any, tuple[float, float], float]] = []
        self.goto_calls: list[tuple[Any, list[float], float, float]] = []
        self.current_head = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        self.current_antennas = (0.0, 0.0)

    def get_current_head_pose(self):
        return self.current_head

    def get_current_joint_positions(self):
        return ([], self.current_antennas)

    def set_target(self, *, head, antennas, body_yaw):
        self.current_head = head
        self.current_antennas = (float(antennas[0]), float(antennas[1]))
        self.set_target_calls.append((head, self.current_antennas, float(body_yaw)))

    def goto_target(self, *, head, antennas, duration, body_yaw):
        self.goto_calls.append((head, list(antennas), float(duration), float(body_yaw)))


class MovementManagerTest(unittest.TestCase):
    def test_idle_progresses_to_phrase_based_motion(self) -> None:
        robot = _FakeRobot()
        manager = MovementManager(
            robot,
            idle_inactivity_delay=0.01,
            idle_first_delay=0.05,
            idle_glance_interval=0.2,
            target_frequency=50.0,
        )
        manager.set_idle_phrase_candidates(["look"])
        manager.state.last_activity_time = time.monotonic() - 8.0
        manager._last_idle_phrase_at = time.monotonic() - 1.0  # type: ignore[attr-defined]
        manager.start()
        try:
            time.sleep(0.5)
            self.assertTrue(robot.set_target_calls)
            antenna_samples = [call[1] for call in robot.set_target_calls]
            self.assertTrue(any(abs(left) > 0.0 or abs(right) > 0.0 for left, right in antenna_samples))
        finally:
            manager.stop()

    def test_speech_offsets_keep_antennas_bounded(self) -> None:
        robot = _FakeRobot()
        manager = MovementManager(robot, idle_inactivity_delay=10.0, target_frequency=50.0)
        manager.start()
        try:
            manager.queue_move(build_gesture_move("look", __import__("random").Random(2)))
            manager.set_speech_offsets((0.0, 0.0, 0.0, 0.1, 0.2, 0.3))
            manager.set_speaking(True)
            time.sleep(0.1)
            head, antennas, _ = robot.set_target_calls[-1]
            self.assertLessEqual(max(abs(antennas[0]), abs(antennas[1])), 0.12)
            self.assertNotEqual(head, create_head_pose(0, 0, 0, 0, 0, 0, degrees=True))
        finally:
            manager.stop()

    def test_speaking_does_not_freeze_antennas_hard(self) -> None:
        robot = _FakeRobot()
        manager = MovementManager(robot, idle_inactivity_delay=0.01, idle_first_delay=0.05, idle_glance_interval=0.2, target_frequency=50.0)
        manager.start()
        try:
            move = build_gesture_move("look", __import__("random").Random(1))
            assert move is not None
            manager.queue_move(move)
            time.sleep(0.2)
            before = robot.set_target_calls[-1][1]
            manager.set_speaking(True)
            time.sleep(0.1)
            during = robot.set_target_calls[-1][1]
            self.assertLessEqual(max(abs(during[0]), abs(during[1])), 0.12)
            manager.set_speaking(False)
            time.sleep(0.3)
            after = robot.set_target_calls[-1][1]
            self.assertNotEqual(after, before)
        finally:
            manager.stop()

    def test_queue_move_and_stop_resets_neutral(self) -> None:
        robot = _FakeRobot()
        manager = MovementManager(robot, idle_inactivity_delay=10.0, target_frequency=50.0)
        manager.start()
        try:
            move = build_gesture_move("nod", __import__("random").Random(1))
            assert move is not None
            manager.queue_move(move)
            time.sleep(0.2)
            self.assertTrue(robot.set_target_calls)
        finally:
            manager.stop()
        self.assertTrue(robot.goto_calls)


if __name__ == "__main__":
    unittest.main()
