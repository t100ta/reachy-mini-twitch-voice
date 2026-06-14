"""Tests for TTS 429 rate-limit resilience in ReachySdkAdapter."""
from __future__ import annotations

import unittest
from http.client import HTTPMessage
from unittest.mock import MagicMock, patch
import urllib.error

from reachy_twitch_voice.reachy_adapter import ReachySdkAdapter, _parse_retry_after


def _make_http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    """Build a fake urllib.error.HTTPError with optional Retry-After header."""
    hdrs = HTTPMessage()
    if retry_after is not None:
        hdrs["Retry-After"] = retry_after
    return urllib.error.HTTPError(url="", code=code, msg="Error", hdrs=hdrs, fp=None)


def _make_adapter() -> ReachySdkAdapter:
    """Create a ReachySdkAdapter configured for openai-tts with a fake key."""
    adapter = ReachySdkAdapter(
        host="127.0.0.1",
        tts_engine="openai-tts",
        openai_api_key="sk-test",
    )
    # Mark as ready so speak() would work if called
    adapter._ready = True
    return adapter


# ---------------------------------------------------------------------------
# Retry / fallback tests
# ---------------------------------------------------------------------------


class TtsResilienceTest(unittest.TestCase):

    def test_429_retry_then_success(self) -> None:
        """Two 429s followed by a valid response — espeak never called."""
        adapter = _make_adapter()

        # Build a minimal valid WAV (44-byte header + silence) so the size check passes
        import io, wave, struct
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(struct.pack("<h", 0) * 100)
        valid_wav_bytes = buf.getvalue()

        side_effects = [
            _make_http_error(429),
            _make_http_error(429),
            valid_wav_bytes,
        ]

        call_count = 0

        def fake_request(payload):
            nonlocal call_count
            val = side_effects[call_count]
            call_count += 1
            if isinstance(val, Exception):
                raise val
            return val

        with patch.object(adapter, "_request_openai_tts", side_effect=fake_request), \
             patch.object(adapter, "_synthesize_with_espeak") as mock_espeak, \
             patch("reachy_twitch_voice.reachy_adapter.time.sleep") as mock_sleep, \
             patch.object(adapter, "_normalize_wav_for_playback"):

            result = adapter._synthesize_with_openai_tts("テスト")

        mock_espeak.assert_not_called()
        self.assertEqual(mock_sleep.call_count, 2)
        self.assertIsNotNone(result)

    def test_429_exhausted_falls_back_to_espeak(self) -> None:
        """All 3 attempts raise 429 — espeak-ng called once, correct path returned."""
        adapter = _make_adapter()

        with patch.object(adapter, "_request_openai_tts", side_effect=_make_http_error(429)), \
             patch.object(adapter, "_synthesize_with_espeak", return_value="/tmp/fake.wav") as mock_espeak, \
             patch("reachy_twitch_voice.reachy_adapter.time.sleep"):

            result = adapter._synthesize_with_openai_tts("テスト")

        mock_espeak.assert_called_once()
        self.assertEqual(result, "/tmp/fake.wav")

    def test_retry_after_header_respected(self) -> None:
        """Retry-After: 2 on first 429 — sleep called with value close to 2.0."""
        adapter = _make_adapter()

        import io, wave, struct
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(struct.pack("<h", 0) * 100)
        valid_wav_bytes = buf.getvalue()

        call_count = 0

        def fake_request(payload):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_http_error(429, retry_after="2")
            return valid_wav_bytes

        sleep_calls: list[float] = []

        with patch.object(adapter, "_request_openai_tts", side_effect=fake_request), \
             patch.object(adapter, "_synthesize_with_espeak") as mock_espeak, \
             patch("reachy_twitch_voice.reachy_adapter.time.sleep", side_effect=lambda t: sleep_calls.append(t)), \
             patch.object(adapter, "_normalize_wav_for_playback"):

            adapter._synthesize_with_openai_tts("テスト")

        mock_espeak.assert_not_called()
        self.assertEqual(len(sleep_calls), 1)
        # Retry-After was 2s plus up-to-0.3s jitter
        self.assertAlmostEqual(sleep_calls[0], 2.0, delta=0.4)

    def test_urlerror_falls_back_to_espeak(self) -> None:
        """URLError (network failure) → espeak-ng fallback."""
        adapter = _make_adapter()

        with patch.object(adapter, "_request_openai_tts",
                          side_effect=urllib.error.URLError("network error")), \
             patch.object(adapter, "_synthesize_with_espeak", return_value="/tmp/fake.wav") as mock_espeak:

            result = adapter._synthesize_with_openai_tts("テスト")

        mock_espeak.assert_called_once()
        self.assertEqual(result, "/tmp/fake.wav")

    def test_400_speed_retry(self) -> None:
        """HTTP 400 with speed in payload → speed removed and retried; espeak not called."""
        adapter = _make_adapter()

        import io, wave, struct
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(22050)
            wf.writeframes(struct.pack("<h", 0) * 100)
        valid_wav_bytes = buf.getvalue()

        captured_payloads: list[dict] = []
        call_count = 0

        def fake_request(payload):
            nonlocal call_count
            captured_payloads.append(dict(payload))
            call_count += 1
            if call_count == 1:
                raise _make_http_error(400)
            return valid_wav_bytes

        with patch.object(adapter, "_request_openai_tts", side_effect=fake_request), \
             patch.object(adapter, "_synthesize_with_espeak") as mock_espeak, \
             patch.object(adapter, "_normalize_wav_for_playback"):

            adapter._synthesize_with_openai_tts("テスト")

        mock_espeak.assert_not_called()
        self.assertEqual(len(captured_payloads), 2)
        # First attempt has speed
        self.assertIn("speed", captured_payloads[0])
        # Second attempt has speed removed
        self.assertNotIn("speed", captured_payloads[1])


# ---------------------------------------------------------------------------
# _parse_retry_after unit tests
# ---------------------------------------------------------------------------


class ParseRetryAfterTest(unittest.TestCase):

    def test_parse_retry_after_integer(self) -> None:
        """Retry-After: 5 → returns 5.0."""
        headers = MagicMock()
        headers.get.return_value = "5"
        result = _parse_retry_after(headers)
        self.assertEqual(result, 5.0)

    def test_parse_retry_after_missing(self) -> None:
        """Missing Retry-After header → returns None."""
        headers = MagicMock()
        headers.get.return_value = None
        result = _parse_retry_after(headers)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
