import asyncio
import tempfile
import unittest
from unittest.mock import patch

from reachy_twitch_voice.config import ConversationConfig, SafetyConfig
from reachy_twitch_voice.conversation_session import OpenAIRealtimeSession
from reachy_twitch_voice.input_adapter import TwitchChatInputAdapter
from reachy_twitch_voice.tool_executor import ToolExecutor
from reachy_twitch_voice.types import ConversationInputEvent, ConversationOutputEvent, TwitchMessage


class ConversationFlowTest(unittest.TestCase):
    def test_input_adapter_maps_twitch_message(self) -> None:
        msg = TwitchMessage(
            id="m1",
            channel="chan",
            user_id="u1",
            user_name="alice",
            text="hello",
            received_at=1.0,
        )
        adapter = TwitchChatInputAdapter()
        ev = adapter.to_conversation_input(msg)
        self.assertEqual(ev.message_id, "m1")
        self.assertEqual(ev.user_name, "alice")
        self.assertEqual(ev.text, "hello")
        self.assertEqual(ev.source, "twitch")
        self.assertEqual(ev.queue_age_ms, 0.0)

    def test_tool_executor_emotion_mapping(self) -> None:
        ex = ToolExecutor()
        g1 = ex.pick_gesture(ConversationOutputEvent("r", "joy", []))
        g2 = ex.pick_gesture(ConversationOutputEvent("r", "surprise", []))
        g3 = ex.pick_gesture(ConversationOutputEvent("r", "empathy", []))
        self.assertIn(g1, {"nod", "sway", "tilt"})
        self.assertIn(g2, {"look", "tilt", "nod"})
        self.assertIn(g3, {"nod", "tilt", "look", "sway"})
        self.assertNotEqual(g1, g2)

    def test_tool_executor_tool_call_override(self) -> None:
        ex = ToolExecutor()
        self.assertIn(
            ex.pick_gesture(ConversationOutputEvent("ok", "empathy", ["dance.short"])),
            {"sway", "nod", "look", "tilt"},
        )
        self.assertIn(
            ex.pick_gesture(ConversationOutputEvent("ok", "joy", ["move.left"])),
            {"look", "nod", "tilt"},
        )


class SessionFallbackTest(unittest.TestCase):
    def test_fallback_when_api_key_missing(self) -> None:
        cfg = ConversationConfig(openai_api_key="", context_window_size=30)
        sess = OpenAIRealtimeSession(cfg, SafetyConfig())
        ev = ConversationInputEvent(
            message_id="m1", user_name="alice", channel="chan", text="hello", received_at=1.0
        )
        out = asyncio.run(sess.generate(ev))
        self.assertTrue(out.reply_text)
        self.assertEqual(out.emotion, "empathy")

    def test_fallback_when_openai_timeout(self) -> None:
        cfg = ConversationConfig(openai_api_key="sk-test", context_window_size=30)
        sess = OpenAIRealtimeSession(cfg, SafetyConfig())
        ev = ConversationInputEvent(
            message_id="m2", user_name="bob", channel="chan", text="hello", received_at=1.0
        )

        def _raise_timeout(_: OpenAIRealtimeSession, __: ConversationInputEvent) -> str:
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
            message_id="m3", user_name="alice", channel="chan", text="hello", received_at=1.0
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
            message_id="m4", user_name="alice", channel="chan", text="hello", received_at=1.0
        )
        prompt = sess._build_prompt(ev, "")
        self.assertIn("name=Reloaded", prompt)


if __name__ == "__main__":
    unittest.main()
