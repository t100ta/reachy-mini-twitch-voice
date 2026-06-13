import asyncio
import io
import json
import os
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from reachy_twitch_voice.config import (
    ConversationConfig,
    HermesConfig,
    SafetyConfig,
    ViewerMemoryConfig,
)
from reachy_twitch_voice.hermes_session import (
    FALLBACK_REPLY,
    HermesConversationSession,
    _coerce_emotion,
    _viewer_key,
)
from reachy_twitch_voice.types import ConversationInputEvent
from reachy_twitch_voice.viewer_memory_store import (
    NoopViewerMemoryStore,
    ViewerMemoryStore,
)


def _build_event(
    *,
    user_name: str = "alice",
    user_id: str | None = "12345",
    display_name: str | None = "Alice",
    text: str = "こんにちは",
    source: str = "twitch",
    is_operator: bool = False,
) -> ConversationInputEvent:
    return ConversationInputEvent(
        message_id="m-1",
        user_name=user_name,
        display_name=display_name,
        channel="chan",
        text=text,
        received_at=time.time(),
        is_operator=is_operator,
        source=source,  # type: ignore[arg-type]
        queue_age_ms=0.0,
        user_id=user_id,
    )


def _make_session(
    *,
    api_key: str = "test-key",
    viewer_store=None,
    viewer_memory_cfg: ViewerMemoryConfig | None = None,
) -> HermesConversationSession:
    cfg = ConversationConfig(openai_api_key="", engine="hermes")
    safety = SafetyConfig()
    hermes = HermesConfig(
        base_url="http://example.invalid/v1",
        api_key=api_key,
        model="hermes-test",
        timeout_sec=1.0,
    )
    return HermesConversationSession(
        cfg=cfg,
        safety_cfg=safety,
        hermes_cfg=hermes,
        viewer_store=viewer_store or NoopViewerMemoryStore(),
        viewer_memory_cfg=viewer_memory_cfg or ViewerMemoryConfig(),
    )


def _hermes_chat_response(content: str) -> dict:
    return {
        "id": "resp-1",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": content}},
        ],
    }


def _fake_urlopen(response_obj: dict):
    """Build an urlopen replacement that returns the given dict as JSON."""
    body = json.dumps(response_obj).encode("utf-8")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return body

    return MagicMock(return_value=_Resp())


class HermesConversationSessionTest(unittest.TestCase):
    # ------------------------------------------------------------------
    # Pure helpers
    # ------------------------------------------------------------------

    def test_emotion_neutral_maps_to_empathy(self) -> None:
        self.assertEqual(_coerce_emotion("neutral"), "empathy")
        self.assertEqual(_coerce_emotion("Neutral"), "empathy")
        self.assertEqual(_coerce_emotion("unknown_value"), "empathy")
        self.assertEqual(_coerce_emotion("joy"), "joy")
        self.assertEqual(_coerce_emotion("surprise"), "surprise")

    def test_viewer_key_prefers_user_id(self) -> None:
        ev = _build_event(user_id="42", user_name="alice")
        self.assertEqual(_viewer_key(ev), "42")

    def test_viewer_key_falls_back_to_login_when_no_user_id(self) -> None:
        ev = _build_event(user_id=None, user_name="Alice")
        self.assertEqual(_viewer_key(ev), "alice")

    def test_viewer_key_uses_unknown_when_blank(self) -> None:
        ev = _build_event(user_id="   ", user_name="   ")
        self.assertEqual(_viewer_key(ev), "unknown")

    # ------------------------------------------------------------------
    # generate() with mocked urlopen
    # ------------------------------------------------------------------

    def test_missing_api_key_returns_fallback(self) -> None:
        sess = _make_session(api_key="")
        out = asyncio.run(sess.generate(_build_event()))
        self.assertEqual(out.reply_text, FALLBACK_REPLY)
        self.assertEqual(out.emotion, "empathy")

    def test_successful_response_parses_to_output(self) -> None:
        sess = _make_session()
        content = json.dumps(
            {
                "should_speak": True,
                "text": "やっほー、アリス！",
                "emotion": "joy",
                "memory_updates": [],
            }
        )
        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            _fake_urlopen(_hermes_chat_response(content)),
        ):
            out = asyncio.run(sess.generate(_build_event()))
        self.assertEqual(out.reply_text, "やっほー、アリス！")
        self.assertEqual(out.emotion, "joy")

    def test_response_with_extra_text_is_extracted(self) -> None:
        sess = _make_session()
        content = (
            "Sure! Here is the JSON:\n"
            "```\n"
            '{"should_speak": true, "text": "ようこそ", "emotion": "neutral",'
            ' "memory_updates": []}\n'
            "```\n"
        )
        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            _fake_urlopen(_hermes_chat_response(content)),
        ):
            out = asyncio.run(sess.generate(_build_event()))
        self.assertEqual(out.reply_text, "ようこそ")
        # neutral → empathy mapping
        self.assertEqual(out.emotion, "empathy")

    def test_http_error_falls_back(self) -> None:
        sess = _make_session()

        def _raise(*args, **kwargs):
            raise urllib.error.HTTPError(
                url="http://example.invalid/v1/chat/completions",
                code=500,
                msg="boom",
                hdrs=None,  # type: ignore[arg-type]
                fp=io.BytesIO(b""),
            )

        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            side_effect=_raise,
        ):
            out = asyncio.run(sess.generate(_build_event()))
        self.assertEqual(out.reply_text, FALLBACK_REPLY)

    def test_timeout_falls_back(self) -> None:
        sess = _make_session()

        def _raise(*args, **kwargs):
            raise TimeoutError("slow")

        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            side_effect=_raise,
        ):
            out = asyncio.run(sess.generate(_build_event()))
        self.assertEqual(out.reply_text, FALLBACK_REPLY)

    def test_should_speak_false_yields_empty_reply(self) -> None:
        sess = _make_session()
        content = json.dumps(
            {
                "should_speak": False,
                "text": "",
                "emotion": "empathy",
                "memory_updates": [],
            }
        )
        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            _fake_urlopen(_hermes_chat_response(content)),
        ):
            out = asyncio.run(sess.generate(_build_event()))
        self.assertEqual(out.reply_text, "")
        self.assertEqual(out.emotion, "empathy")

    def test_input_mode_mapping(self) -> None:
        sess = _make_session()
        captured = {}

        def _capture_urlopen(req, timeout):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            body = json.dumps(
                _hermes_chat_response(
                    json.dumps(
                        {
                            "should_speak": True,
                            "text": "hi",
                            "emotion": "joy",
                            "memory_updates": [],
                        }
                    )
                )
            ).encode("utf-8")

            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return body

            return _Resp()

        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            side_effect=_capture_urlopen,
        ):
            asyncio.run(sess.generate(_build_event(source="manual")))

        user_msg = json.loads(captured["body"]["messages"][1]["content"])
        self.assertEqual(user_msg["input_mode"], "manual_text")

        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            side_effect=_capture_urlopen,
        ):
            asyncio.run(sess.generate(_build_event(source="twitch_event")))
        user_msg2 = json.loads(captured["body"]["messages"][1]["content"])
        self.assertEqual(user_msg2["input_mode"], "twitch")
        self.assertEqual(user_msg2["event_type"], "channel_event")

    def test_memory_updates_persisted_via_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "viewer_memory.sqlite3")
            store = ViewerMemoryStore(db_path, max_notes=5)
            sess = _make_session(viewer_store=store)
            content = json.dumps(
                {
                    "should_speak": True,
                    "text": "了解、マロって呼ぶね。",
                    "emotion": "joy",
                    "memory_updates": [
                        {
                            "kind": "preferred_name",
                            "value": "マロ",
                            "reason": "本人が指定",
                            "confidence": 0.95,
                        },
                        {
                            "kind": "note",
                            "value": "Alan Wake 2 が好き",
                            "reason": "趣味",
                            "confidence": 0.7,
                        },
                    ],
                }
            )
            with patch(
                "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
                _fake_urlopen(_hermes_chat_response(content)),
            ):
                asyncio.run(sess.generate(_build_event(user_id="42")))

            prof = store.get_profile("42")
            assert prof is not None
            self.assertEqual(prof.preferred_name, "マロ")
            notes = store.list_recent_notes(viewer_key="42", limit=10)
            self.assertEqual(len(notes), 1)
            self.assertEqual(notes[0].note, "Alan Wake 2 が好き")

    def test_memory_failure_does_not_block_speech(self) -> None:
        store = NoopViewerMemoryStore()
        # Force set_preferred_name to raise; should not derail the generate path.
        with patch.object(
            store, "set_preferred_name", side_effect=ValueError("nope")
        ):
            sess = _make_session(viewer_store=store)
            content = json.dumps(
                {
                    "should_speak": True,
                    "text": "短い返答",
                    "emotion": "empathy",
                    "memory_updates": [
                        {
                            "kind": "preferred_name",
                            "value": "マロ",
                            "reason": "",
                            "confidence": 0.9,
                        }
                    ],
                }
            )
            with patch(
                "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
                _fake_urlopen(_hermes_chat_response(content)),
            ):
                out = asyncio.run(sess.generate(_build_event()))
        self.assertEqual(out.reply_text, "短い返答")

    def test_unparseable_response_falls_back(self) -> None:
        sess = _make_session()
        content = "this is just plain text with no JSON"
        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            _fake_urlopen(_hermes_chat_response(content)),
        ):
            out = asyncio.run(sess.generate(_build_event()))
        self.assertEqual(out.reply_text, FALLBACK_REPLY)

    def test_post_safety_blocks_unsafe_text(self) -> None:
        sess = _make_session()
        content = json.dumps(
            {
                "should_speak": True,
                "text": "あなたの住所を教えて",
                "emotion": "empathy",
                "memory_updates": [],
            }
        )
        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            _fake_urlopen(_hermes_chat_response(content)),
        ):
            out = asyncio.run(sess.generate(_build_event()))
        # post_safety blocks → fallback
        self.assertEqual(out.reply_text, FALLBACK_REPLY)

    # ------------------------------------------------------------------
    # B-2: Session-level first-visit deduplication
    # ------------------------------------------------------------------

    def test_increment_visit_only_once_per_session(self) -> None:
        """increment_visit should only fire on the first message per viewer per session."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "viewer_memory.sqlite3")
            store = ViewerMemoryStore(db_path, max_notes=5)
            sess = _make_session(viewer_store=store)

            content = json.dumps(
                {"should_speak": True, "text": "hi", "emotion": "joy", "memory_updates": []}
            )
            fake = _fake_urlopen(_hermes_chat_response(content))
            with patch("reachy_twitch_voice.hermes_session.urllib.request.urlopen", fake):
                asyncio.run(sess.generate(_build_event(user_id="99")))
            with patch("reachy_twitch_voice.hermes_session.urllib.request.urlopen", fake):
                asyncio.run(sess.generate(_build_event(user_id="99")))

            prof = store.get_profile("99")
            assert prof is not None
            # Should be 1, not 2
            self.assertEqual(prof.visit_count, 1)

    # ------------------------------------------------------------------
    # B-3: Payload extended fields
    # ------------------------------------------------------------------

    def test_payload_contains_visit_count_and_is_returning(self) -> None:
        """Payload should include visit_count, is_returning, days_since_last_visit, last_topic."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "viewer_memory.sqlite3")
            store = ViewerMemoryStore(db_path, max_notes=5)
            sess = _make_session(viewer_store=store)
            captured: dict = {}

            def _capture(req, timeout):
                captured["body"] = json.loads(req.data.decode("utf-8"))
                body = json.dumps(
                    _hermes_chat_response(
                        json.dumps({"should_speak": True, "text": "hi", "emotion": "joy", "memory_updates": []})
                    )
                ).encode("utf-8")

                class _Resp:
                    def __enter__(self):
                        return self

                    def __exit__(self, *a):
                        return False

                    def read(self):
                        return body

                return _Resp()

            with patch("reachy_twitch_voice.hermes_session.urllib.request.urlopen", side_effect=_capture):
                asyncio.run(sess.generate(_build_event(user_id="u42")))

            user_msg = json.loads(captured["body"]["messages"][1]["content"])
            viewer = user_msg["viewer"]
            self.assertIn("visit_count", viewer)
            self.assertIn("is_returning", viewer)
            self.assertIn("is_first_message_this_session", viewer)
            self.assertIn("days_since_last_visit", viewer)
            self.assertIn("last_topic", viewer)
            self.assertTrue(viewer["is_first_message_this_session"])

    # ------------------------------------------------------------------
    # B-4: kind="topic" memory update
    # ------------------------------------------------------------------

    def test_memory_update_topic_sets_last_topic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "viewer_memory.sqlite3")
            store = ViewerMemoryStore(db_path, max_notes=5)
            sess = _make_session(viewer_store=store)
            content = json.dumps(
                {
                    "should_speak": True,
                    "text": "ポケモンいいよね",
                    "emotion": "joy",
                    "memory_updates": [
                        {
                            "kind": "topic",
                            "value": "ポケモン新作の話",
                            "reason": "会話テーマ",
                            "confidence": 0.8,
                        }
                    ],
                }
            )
            with patch(
                "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
                _fake_urlopen(_hermes_chat_response(content)),
            ):
                asyncio.run(sess.generate(_build_event(user_id="u1")))

            prof = store.get_profile("u1")
            assert prof is not None
            self.assertEqual(prof.last_topic, "ポケモン新作の話")

    # ------------------------------------------------------------------
    # Phase A: FALLBACK_REPLIES rotation and retry
    # ------------------------------------------------------------------

    def test_fallback_replies_rotate(self) -> None:
        from reachy_twitch_voice.hermes_session import FALLBACK_REPLIES
        sess = _make_session(api_key="")
        replies = []
        for _ in range(len(FALLBACK_REPLIES)):
            out = asyncio.run(sess.generate(_build_event()))
            replies.append(out.reply_text)
        # All replies should come from FALLBACK_REPLIES
        for r in replies:
            self.assertIn(r, FALLBACK_REPLIES)

    def test_fallback_reply_alias_is_first(self) -> None:
        from reachy_twitch_voice.hermes_session import FALLBACK_REPLIES
        self.assertEqual(FALLBACK_REPLY, FALLBACK_REPLIES[0])

    def test_retry_on_network_error(self) -> None:
        """Session retries once on URLError before falling back."""
        sess = _make_session()
        # HermesConfig default retry_count=0; override to 1 for this test
        sess.hermes_cfg = type(sess.hermes_cfg)(
            base_url="http://example.invalid/v1",
            api_key="test-key",
            model="hermes-test",
            timeout_sec=1.0,
            retry_count=1,
        )
        call_count = [0]

        def _flaky(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise urllib.error.URLError("transient")
            # Second call succeeds
            body = json.dumps(
                _hermes_chat_response(
                    json.dumps({"should_speak": True, "text": "ok", "emotion": "joy", "memory_updates": []})
                )
            ).encode("utf-8")

            class _Resp:
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def read(self):
                    return body

            return _Resp()

        with patch("reachy_twitch_voice.hermes_session.urllib.request.urlopen", side_effect=_flaky):
            with patch("reachy_twitch_voice.hermes_session.asyncio.sleep", return_value=None):
                out = asyncio.run(sess.generate(_build_event()))

        self.assertEqual(out.reply_text, "ok")
        self.assertEqual(call_count[0], 2)

    # ------------------------------------------------------------------
    # C-3/C-4: session_log and generate_stream_summary
    # ------------------------------------------------------------------

    def test_session_log_populated_after_generate(self) -> None:
        sess = _make_session()
        content = json.dumps(
            {"should_speak": True, "text": "hello", "emotion": "joy", "memory_updates": []}
        )
        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            _fake_urlopen(_hermes_chat_response(content)),
        ):
            asyncio.run(sess.generate(_build_event()))
        self.assertEqual(len(sess.session_log), 1)
        entry = sess.session_log[0]
        self.assertIn("user_name", entry)
        self.assertIn("text", entry)
        self.assertIn("reply", entry)
        self.assertIn("emotion", entry)
        self.assertIn("at", entry)

    def test_generate_stream_summary_success(self) -> None:
        sess = _make_session()
        # Populate session_log with enough entries
        content = json.dumps(
            {"should_speak": True, "text": "hi", "emotion": "joy", "memory_updates": []}
        )
        fake = _fake_urlopen(_hermes_chat_response(content))
        for _ in range(4):
            with patch("reachy_twitch_voice.hermes_session.urllib.request.urlopen", fake):
                asyncio.run(sess.generate(_build_event()))

        summary_response = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "summary": "楽しい配信でした",
                            "highlights": ["レイドが来た"],
                            "learnings": ["コメント速度は遅めがよい"],
                        })
                    }
                }
            ]
        }
        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            _fake_urlopen(summary_response),
        ):
            result = asyncio.run(sess.generate_stream_summary(min_turns=3))

        assert result is not None
        self.assertEqual(result["summary"], "楽しい配信でした")
        self.assertIn("レイドが来た", result["highlights"])

    def test_generate_stream_summary_min_turns_not_met(self) -> None:
        sess = _make_session()
        # Only 1 turn in session_log
        content = json.dumps(
            {"should_speak": True, "text": "hi", "emotion": "joy", "memory_updates": []}
        )
        with patch(
            "reachy_twitch_voice.hermes_session.urllib.request.urlopen",
            _fake_urlopen(_hermes_chat_response(content)),
        ):
            asyncio.run(sess.generate(_build_event()))

        result = asyncio.run(sess.generate_stream_summary(min_turns=3))
        self.assertIsNone(result)

    def test_generate_stream_summary_network_failure_returns_none(self) -> None:
        sess = _make_session()
        # Populate enough turns
        content = json.dumps(
            {"should_speak": True, "text": "hi", "emotion": "joy", "memory_updates": []}
        )
        fake = _fake_urlopen(_hermes_chat_response(content))
        for _ in range(4):
            with patch("reachy_twitch_voice.hermes_session.urllib.request.urlopen", fake):
                asyncio.run(sess.generate(_build_event()))

        def _fail(*args, **kwargs):
            raise urllib.error.URLError("network down")

        with patch("reachy_twitch_voice.hermes_session.urllib.request.urlopen", side_effect=_fail):
            result = asyncio.run(sess.generate_stream_summary(min_turns=3))
        self.assertIsNone(result)

    def test_generate_stream_summary_no_api_key_returns_none(self) -> None:
        sess = _make_session(api_key="")
        result = asyncio.run(sess.generate_stream_summary(min_turns=0))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
