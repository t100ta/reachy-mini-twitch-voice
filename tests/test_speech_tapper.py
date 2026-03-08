import struct
import unittest

from reachy_twitch_voice.speech_tapper import frames_from_wav


class SpeechTapperTest(unittest.TestCase):
    def test_frames_from_wav_returns_motion_frames(self) -> None:
        sr = 16000
        channels = 1
        # 0.5 sec of 440Hz sine-like waveform (int16)
        samples = []
        for i in range(sr // 2):
            v = int(12000 * ((i % 36) / 36.0 - 0.5))
            samples.append(v)
        raw = struct.pack("<" + "h" * len(samples), *samples)

        frames = frames_from_wav(raw, sr, channels)
        self.assertGreater(len(frames), 0)
        self.assertIn("pitch_deg", frames[0])
        self.assertIn("yaw_deg", frames[0])
        self.assertIn("roll_deg", frames[0])
        self.assertIn("gain", frames[0])


if __name__ == "__main__":
    unittest.main()
