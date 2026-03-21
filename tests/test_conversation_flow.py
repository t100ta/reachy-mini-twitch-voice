import asyncio
import tempfile
import unittest
from unittest.mock import patch

from reachy_twitch_voice.config import ConversationConfig, SafetyConfig
from reachy_twitch_voice.conversation_session import OpenAIRealtimeSession
from reachy_twitch_voice.input_adapter import ManualTextInputAdapter, TwitchChatInputAdapter
from reachy_twitch_voice.tool_executor import ToolExecutor
from reachy_twitch_voice.types import ConversationInputEvent, ConversationOutputEvent, TwitchMessage


class ConversationFlowTest(unittest.TestCase):
    def test_input_adapter_maps_twitch_message(self) -> None:
        msg = TwitchMessage(
            id="m1",
            channel="chan",
            user_id="u1",
            user_name="alice",
            display_name="Alice",
            text="hello",
            received_at=1.0,
        )
        adapter = TwitchChatInputAdapter()
        ev = adapter.to_conversation_input(msg)
        self.assertEqual(ev.message_id, "m1")
        self.assertEqual(ev.user_name, "alice")
        self.assertEqual(ev.display_name, "Alice")
        self.assertEqual(ev.text, "hello")
        self.assertEqual(ev.source, "twitch")
        self.assertEqual(ev.queue_age_ms, 0.0)

    def test_manual_input_adapter_builds_manual_event(self) -> None:
        adapter = ManualTextInputAdapter()
        ev = adapter.build_event("hello", user_name="tester")
        self.assertEqual(ev.user_name, "tester")
        self.assertEqual(ev.display_name, "tester")
        self.assertEqual(ev.channel, "manual")
        self.assertEqual(ev.text, "hello")
        self.assertEqual(ev.source, "manual")
        self.assertTrue(ev.message_id)

    def test_reply_decoration_uses_display_name_naturally(self) -> None:
        sess = OpenAIRealtimeSession(ConversationConfig(openai_api_key=""), SafetyConfig())
        ev = ConversationInputEvent(
            message_id="address-user-case",
            user_name="alice",
            display_name="ありす",
            channel="chan",
            text="それ面白いね",
            received_at=1.0,
        )
        decorated = sess._decorate_reply(ev, "そうなんだよね")
        self.assertIn("ありす", decorated)

    def test_prompt_includes_display_name(self) -> None:
        sess = OpenAIRealtimeSession(ConversationConfig(openai_api_key=""), SafetyConfig())
        ev = ConversationInputEvent(
            message_id="m-display",
            user_name="alice",
            display_name="Alice",
            channel="chan",
            text="hello",
            received_at=1.0,
        )
        prompt = sess._build_prompt(ev, "")
        self.assertIn("display_name=Alice", prompt)

    def test_tool_executor_emotion_mapping(self) -> None:
        ex = ToolExecutor()
        p1 = ex.build_motion_plan(ConversationOutputEvent("r", "joy", []))
        p2 = ex.build_motion_plan(ConversationOutputEvent("r", "surprise", []))
        p3 = ex.build_motion_plan(ConversationOutputEvent("r", "empathy", []))
        self.assertIn(p1.fallback_gesture, {"nod", "sway", "tilt"})
        self.assertIn(p2.fallback_gesture, {"look", "tilt", "nod"})
        self.assertIn(p3.fallback_gesture, {"nod", "tilt", "look", "sway"})
        self.assertEqual(p1.speech_opening_emotion, "happy")
        self.assertEqual(p2.speech_opening_emotion, "surprised")
        self.assertIn(p3.speech_opening_emotion, {"listening", "agree"})

    def test_tool_executor_tool_call_override(self) -> None:
        ex = ToolExecutor()
        dance = ex.build_motion_plan(ConversationOutputEvent("ok", "empathy", ["dance.short"]))
        left = ex.build_motion_plan(ConversationOutputEvent("ok", "joy", ["move.left"]))
        self.assertIn(dance.fallback_gesture, {"sway", "nod", "look", "tilt"})
        self.assertEqual(dance.dance_move, "simple_nod")
        self.assertIn(left.fallback_gesture, {"look", "nod", "tilt"})

    def test_tool_executor_new_action_names(self) -> None:
        ex = ToolExecutor()
        dance_short = ex.build_motion_plan(ConversationOutputEvent("ok", "joy", ["dance_short"]))
        move_left = ex.build_motion_plan(ConversationOutputEvent("ok", "joy", ["move_left"]))
        move_up = ex.build_motion_plan(ConversationOutputEvent("ok", "empathy", ["move_up"]))
        self.assertEqual(dance_short.dance_move, "simple_nod")
        self.assertIn(move_left.fallback_gesture, {"look", "nod", "tilt"})
        self.assertIn(move_up.fallback_gesture, {"nod", "look", "tilt"})


class SessionFallbackTest(unittest.TestCase):
    def test_fallback_when_api_key_missing(self) -> None:
        cfg = ConversationConfig(openai_api_key="", context_window_size=30)
        sess = OpenAIRealtimeSession(cfg, SafetyConfig())
        ev = ConversationInputEvent(
            message_id="m1", user_name="alice", display_name=None, channel="chan", text="hello", received_at=1.0
        )
        out = asyncio.run(sess.generate(ev))
        self.assertTrue(out.reply_text)
        self.assertEqual(out.emotion, "empathy")

    def test_fallback_when_openai_timeout(self) -> None:
        cfg = ConversationConfig(openai_api_key="sk-test", context_window_size=30)
        sess = OpenAIRealtimeSession(cfg, SafetyConfig())
        ev = ConversationInputEvent(
            message_id="m2", user_name="bob", display_name=None, channel="chan", text="hello", received_at=1.0
        )

        def _raise_timeout(self: OpenAIRealtimeSession, event: ConversationInputEvent) -> tuple[str, list[str]]:
            raise TimeoutError("timed out")

        with patch.object(OpenAIRealtimeSession, "_call_openai", _raise_timeout):
            out = asyncio.run(sess.generate(ev))
        self.assertTrue(out.reply_text)
        self.assertEqual(out.emotion, "empathy")

    def test_system_prompt_template_replacement(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as f:
            f.write("name={{PERSONA_NAME}} kana={{PERSONA_NAME_KANA}} op={{OPERATOR_NAME}} style={{PERSONA_STYLE}}")
            f.flush()
            cfg = ConversationConfig(
                openai_api_key="",
                context_window_size=30,
                persona_name="NUVA2",
                persona_name_kana="ヌーバ2",
                operator_name="operator-x",
                persona_style="ていねい",
                system_prompt_file=f.name,
            )
            sess = OpenAIRealtimeSession(cfg, SafetyConfig())

        ev = ConversationInputEvent(
            message_id="m3", user_name="alice", display_name=None, channel="chan", text="hello", received_at=1.0
        )
        prompt = sess._build_prompt(ev, "")
        self.assertIn("name=NUVA2", prompt)
        self.assertIn("kana=ヌーバ2", prompt)
        self.assertIn("op=operator-x", prompt)
        self.assertIn("style=ていねい", prompt)

    def test_reload_config_rebuilds_prompt(self) -> None:
        cfg = ConversationConfig(openai_api_key="", context_window_size=30)
        sess = OpenAIRealtimeSession(cfg, SafetyConfig())
        new_cfg = ConversationConfig(
            openai_api_key="",
            context_window_size=30,
            persona_name="Reloaded",
            system_prompt_text="name={{PERSONA_NAME}}",
        )
        asyncio.run(sess.reload_config(new_cfg))
        ev = ConversationInputEvent(
            message_id="m4", user_name="alice", display_name=None, channel="chan", text="hello", received_at=1.0
        )
        prompt = sess._build_prompt(ev, "")
        self.assertIn("name=Reloaded", prompt)


if __name__ == "__main__":
    unittest.main()
