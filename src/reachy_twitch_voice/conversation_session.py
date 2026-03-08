from __future__ import annotations

import asyncio
import importlib.resources as resources
import json
import logging
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import ConversationConfig, SafetyConfig
from .types import ConversationInputEvent, ConversationOutputEvent, ConversationTurn

FALLBACK_REPLY = "コメントありがとう！その話、もう少し詳しく聞かせて。"
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenAIRealtimeSession:
    cfg: ConversationConfig
    safety_cfg: SafetyConfig
    turns: list[ConversationTurn]
    system_prompt: str

    def __init__(self, cfg: ConversationConfig, safety_cfg: SafetyConfig) -> None:
        self.cfg = cfg
        self.safety_cfg = safety_cfg
        self.turns = []
        self.system_prompt = self._load_system_prompt()

    async def generate(self, event: ConversationInputEvent) -> ConversationOutputEvent:
        if not self.cfg.openai_api_key:
            return self._fallback_output()

        try:
            response_text = await asyncio.to_thread(self._call_openai, event)
        except Exception as exc:
            LOGGER.warning("OpenAI generation failed, fallback reply is used: %s", exc)
            return self._fallback_output()
        parsed = self._parse_response(response_text)
        safe = self._post_safety(parsed.reply_text)
        if not safe:
            return self._fallback_output()

        output = ConversationOutputEvent(
            reply_text=safe,
            emotion=parsed.emotion,
            tool_calls=parsed.tool_calls,
        )
        self.turns.append(
            ConversationTurn(
                user_name=event.user_name,
                text=event.text,
                assistant_reply=output.reply_text,
                emotion=output.emotion,
            )
        )
        self.turns = self.turns[-self.cfg.context_window_size :]
        return output

    def _call_openai(self, event: ConversationInputEvent) -> str:
        history_lines = [
            f"{t.user_name}: {t.text}\\nassistant: {t.assistant_reply} ({t.emotion})"
            for t in self.turns[-self.cfg.context_window_size :]
        ]
        history_text = "\\n".join(history_lines)
        prompt = self._build_prompt(event, history_text)
        payload = {
            "model": self.cfg.openai_realtime_model,
            "input": prompt,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.cfg.openai_api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.openai_timeout_sec) as resp:
                body = resp.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            LOGGER.warning("OpenAI HTTP request failed, fallback reply is used: %s", exc)
            return json.dumps(
                {"reply": FALLBACK_REPLY, "emotion": "empathy", "tool_calls": []}
            )

        try:
            parsed = json.loads(body)
            if isinstance(parsed.get("output_text"), str) and parsed["output_text"].strip():
                return parsed["output_text"]
            # Fallback parse for response item format.
            output = parsed.get("output", [])
            if output and isinstance(output, list):
                content = output[0].get("content", [])
                if content and isinstance(content, list):
                    text = content[0].get("text", "")
                    if text:
                        return text
        except Exception:
            pass

        return json.dumps({"reply": FALLBACK_REPLY, "emotion": "empathy", "tool_calls": []})

    def _build_prompt(self, event: ConversationInputEvent, history_text: str) -> str:
        return (
            f"{self.system_prompt}"
            f"\\n[history]\\n{history_text}"
            f"\\n[user] {event.user_name}: {event.text}"
            f"\\n[user_meta] is_operator={str(event.is_operator).lower()}"
        )

    def _load_system_prompt(self) -> str:
        template = (
            "あなたの名前は {{PERSONA_NAME}}（{{PERSONA_NAME_KANA}}）。"
            "作成者の {{OPERATOR_NAME}} は Operator と呼ぶ。"
            "返答は {{PERSONA_STYLE}}。"
            "あなたは配信者として視聴者コメントに返事し、話題を膨らませてください。"
            "出力はJSONのみ。"
            "{\"reply\":string,\"emotion\":\"joy|surprise|empathy\","
            "\"tool_calls\":[string]} 形式で返すこと。"
            "tool_calls は次から0-2個だけ選ぶ: "
            "dance.short, move.left, move.right, move.up, move.down, settle。"
            "不適切・危険・個人情報要求は避けること。"
            "\\n[rule] もし user が Operator なら、Operator と認識したうえで丁寧に応答する。"
        )
        path = self.cfg.system_prompt_file.strip()
        if path:
            try:
                template = Path(path).read_text(encoding="utf-8")
            except OSError as exc:
                LOGGER.warning(
                    "Failed to read SYSTEM_PROMPT_FILE=%s; fallback to packaged prompt: %s",
                    path,
                    exc,
                )
                template = self._read_packaged_prompt_or_default(template)
        else:
            template = self._read_packaged_prompt_or_default(template)
        return (
            template.replace("{{PERSONA_NAME}}", self.cfg.persona_name)
            .replace("{{PERSONA_NAME_KANA}}", self.cfg.persona_name_kana)
            .replace("{{OPERATOR_NAME}}", self.cfg.operator_name)
            .replace("{{PERSONA_STYLE}}", self.cfg.persona_style)
        )

    def _read_packaged_prompt_or_default(self, default_text: str) -> str:
        try:
            return (
                resources.files("reachy_twitch_voice.prompts")
                .joinpath("system_ja.txt")
                .read_text(encoding="utf-8")
            )
        except OSError as exc:
            LOGGER.warning("Failed to read packaged prompt; using built-in prompt: %s", exc)
            return default_text

    def _parse_response(self, raw: str) -> ConversationOutputEvent:
        text = raw.strip()
        # Extract JSON object if the model wrapped it with extra text.
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            text = m.group(0)
        try:
            obj = json.loads(text)
            reply = str(obj.get("reply", FALLBACK_REPLY)).strip() or FALLBACK_REPLY
            emotion = str(obj.get("emotion", "empathy")).strip().lower()
            if emotion not in {"joy", "surprise", "empathy"}:
                emotion = "empathy"
            tool_calls = obj.get("tool_calls", [])
            if not isinstance(tool_calls, list):
                tool_calls = []
            tool_calls = [str(x) for x in tool_calls][:4]
            return ConversationOutputEvent(
                reply_text=reply,
                emotion=emotion,  # type: ignore[arg-type]
                tool_calls=tool_calls,
            )
        except Exception:
            return self._fallback_output()

    def _post_safety(self, text: str) -> str | None:
        if len(text) > max(self.safety_cfg.max_chars, 200):
            text = text[: max(self.safety_cfg.max_chars, 200)]
        low = text.lower()
        for w in self.safety_cfg.ng_words:
            if w.lower() in low:
                return None
        for bad in ["個人情報", "住所", "電話番号", "差別", "暴力"]:
            if bad in text:
                return None
        return text

    def _fallback_output(self) -> ConversationOutputEvent:
        return ConversationOutputEvent(
            reply_text=FALLBACK_REPLY,
            emotion="empathy",
            tool_calls=[],
        )
