from __future__ import annotations

import asyncio
import importlib.resources as resources
import json
import logging
import random
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from .config import ConversationConfig, SafetyConfig
from .types import ConversationInputEvent, ConversationOutputEvent, ConversationTurn

FALLBACK_REPLY = "コメントありがとう！その話、もう少し詳しく聞かせて。"
LOGGER = logging.getLogger(__name__)

_ROBOT_ACTION_TOOL: dict = {
    "type": "function",
    "name": "robot_action",
    "description": "ロボットに動作を実行させます。会話の流れに合わせて適切な動作を選んでください。",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "dance_short",
                    "move_left",
                    "move_right",
                    "move_up",
                    "move_down",
                    "settle",
                ],
                "description": "実行する動作の種類",
            }
        },
        "required": ["action"],
    },
}

_TWITCH_INFO_TOOL: dict = {
    "type": "function",
    "name": "get_twitch_info",
    "description": "Twitch配信のチャンネル情報や視聴者数を取得します。",
    "parameters": {
        "type": "object",
        "properties": {
            "info_type": {
                "type": "string",
                "enum": ["channel_name", "viewer_count"],
                "description": "取得する情報の種類",
            }
        },
        "required": ["info_type"],
    },
}


class ConversationSession(Protocol):
    async def generate(self, event: ConversationInputEvent) -> ConversationOutputEvent:
        ...

    async def reload_config(self, cfg: ConversationConfig) -> None:
        ...


class _OpenAISessionBase:
    def __init__(self, cfg: ConversationConfig, safety_cfg: SafetyConfig) -> None:
        self.cfg = cfg
        self.safety_cfg = safety_cfg
        self.turns: list[ConversationTurn] = []
        self.system_prompt = self._load_system_prompt()
        self._twitch_channel: str = ""
        self._twitch_viewer_count: int | None = None

    async def reload_config(self, cfg: ConversationConfig) -> None:
        self.cfg = cfg
        self.system_prompt = self._load_system_prompt()

    def update_twitch_context(self, channel: str, viewer_count: int | None = None) -> None:
        self._twitch_channel = channel
        self._twitch_viewer_count = viewer_count

    def _build_prompt(self, event: ConversationInputEvent, history_text: str) -> str:
        return (
            f"{self.system_prompt}"
            f"\\n[history]\\n{history_text}"
            f"\\n[user] {event.user_name}: {event.text}"
            f"\\n[user_meta] is_operator={str(event.is_operator).lower()}"
            f" display_name={event.display_name or event.user_name}"
            f" source={event.source}"
            f" queue_age_ms={event.queue_age_ms:.1f}"
        )

    def _load_system_prompt(self) -> str:
        template = (
            "あなたの名前は {{PERSONA_NAME}}（{{PERSONA_NAME_KANA}}）。"
            "作成者の {{OPERATOR_NAME}} は Operator と呼ぶ。"
            "返答は {{PERSONA_STYLE}}。"
            "あなたは配信者として視聴者コメントに返事し、話題を膨らませてください。"
            "返答の冒頭では、毎回ではなく自然な頻度で相手の名前を呼ぶか、コメント内容を短く受けてから本題へ入ってください。"
            "ただし不自然なオウム返しや、毎回同じ接頭辞は避けること。"
            "出力は必ずJSONのみ。ツールを呼んだ後も含め、常に"
            "{\"reply\":string,\"emotion\":\"joy|surprise|empathy\"} 形式のみで返すこと。"
            "プレーンテキストや説明文は絶対に出力しないこと。"
            "ロボットを動かしたい場合は robot_action ツールを呼んでください"
            "（dance_short, move_left, move_right, move_up, move_down から選択。settle は使わなくてよい）。"
            "情報が必要な場合は get_twitch_info ツールを使ってください。"
            "不適切・危険・個人情報要求は避けること。"
            "\\n[rule] もし user が Operator なら、Operator と認識したうえで丁寧に応答する。"
        )
        if self.cfg.system_prompt_text.strip():
            template = self.cfg.system_prompt_text
        else:
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
        prompt = (
            template.replace("{{PERSONA_NAME}}", self.cfg.persona_name)
            .replace("{{PERSONA_NAME_KANA}}", self.cfg.persona_name_kana)
            .replace("{{OPERATOR_NAME}}", self.cfg.operator_name)
            .replace("{{PERSONA_STYLE}}", self.cfg.persona_style)
        )
        if self.cfg.enable_web_search:
            prompt += (
                "\n天気・ニュース・最新情報・知らないことを聞かれたら、"
                "必ず web_search ツールで調べてから回答してください。"
                "知っているつもりで答えず、必ずツールを使うこと。"
            )
        return prompt

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
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            text = m.group(0)
        try:
            obj = json.loads(text)
            reply = str(obj.get("reply", FALLBACK_REPLY)).strip() or FALLBACK_REPLY
            emotion = str(obj.get("emotion", "empathy")).strip().lower()
            if emotion not in {"joy", "surprise", "empathy"}:
                emotion = "empathy"
            return ConversationOutputEvent(
                reply_text=reply,
                emotion=emotion,  # type: ignore[arg-type]
                tool_calls=[],
            )
        except Exception:
            # ツール呼び出し後の継続返答がプレーンテキストになっている場合、そのまま使う
            plain = raw.strip()
            if plain and len(plain) > 3:
                LOGGER.warning("Non-JSON response from LLM, using as plain reply: %r", plain[:120])
                # "(joy)" / "(surprise)" / "(empathy)" suffix から emotion を抽出
                emotion: str = "empathy"
                m_emo = re.search(r"\((\w+)\)\s*$", plain)
                if m_emo:
                    candidate = m_emo.group(1).lower()
                    if candidate in {"joy", "surprise", "empathy"}:
                        emotion = candidate
                        plain = plain[: m_emo.start()].strip()
                return ConversationOutputEvent(
                    reply_text=plain,
                    emotion=emotion,  # type: ignore[arg-type]
                    tool_calls=[],
                )
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

    def _decorate_reply(self, event: ConversationInputEvent, reply_text: str) -> str:
        reply = reply_text.strip()
        if not reply:
            return reply_text

        rng = random.Random(f"{event.message_id}:{event.text}")
        display_name = (event.display_name or event.user_name).strip()
        lowered = reply.lower()
        if display_name and display_name.lower() in lowered:
            return reply

        topic = self._topic_snippet(event.text)
        if event.is_operator:
            picks = [("direct_reply", 0.60), ("mirror_topic", 0.30), ("address_user", 0.10)]
        else:
            picks = [("address_user", 0.35), ("mirror_topic", 0.35), ("direct_reply", 0.30)]

        roll = rng.random()
        cumulative = 0.0
        mode = "direct_reply"
        for candidate, weight in picks:
            cumulative += weight
            if roll <= cumulative:
                mode = candidate
                break

        if mode == "address_user" and display_name:
            return f"{display_name}さん、{reply}"
        if mode == "mirror_topic" and topic:
            return f"{topic}の話、{reply}"
        return reply

    def _topic_snippet(self, text: str) -> str:
        cleaned = re.sub(r"\s+", " ", text).strip(" 　!！?？。.,、")
        if not cleaned:
            return ""
        if len(cleaned) <= 18:
            return cleaned
        for sep in ("、", "。", "！", "?", "？", "!", " ", "　"):
            if sep in cleaned:
                head = cleaned.split(sep, 1)[0].strip()
                if 3 <= len(head) <= 18:
                    return head
        return cleaned[:18].rstrip()

    def _fallback_output(self) -> ConversationOutputEvent:
        return ConversationOutputEvent(
            reply_text=FALLBACK_REPLY,
            emotion="empathy",
            tool_calls=[],
        )

    def _append_turn(self, event: ConversationInputEvent, output: ConversationOutputEvent) -> None:
        self.turns.append(
            ConversationTurn(
                user_name=event.user_name,
                text=event.text,
                assistant_reply=output.reply_text,
                emotion=output.emotion,
            )
        )
        self.turns = self.turns[-self.cfg.context_window_size :]

    def _history_text(self) -> str:
        history_lines = [
            f"{t.user_name}: {t.text}\\nassistant: {t.assistant_reply} ({t.emotion})"
            for t in self.turns[-self.cfg.context_window_size :]
        ]
        return "\\n".join(history_lines)

    def _build_tools_list(self) -> list[dict]:
        if not self.cfg.enable_tools:
            return []
        tools: list[dict] = [_ROBOT_ACTION_TOOL, _TWITCH_INFO_TOOL]
        if self.cfg.enable_web_search:
            tools.insert(0, {"type": "web_search_preview"})
            LOGGER.info("Tools built: web_search_preview + function tools")
        return tools

    def _execute_function_call(
        self, name: str, args: dict, robot_actions: list[str]
    ) -> str:
        if name == "robot_action":
            action = args.get("action", "settle")
            robot_actions.append(action)
            LOGGER.info("Tool call: robot_action action=%s", action)
            return json.dumps({"result": "ok"})
        if name == "get_twitch_info":
            info_type = args.get("info_type", "channel_name")
            LOGGER.info("Tool call: get_twitch_info info_type=%s", info_type)
            if info_type == "channel_name":
                return json.dumps({"channel_name": self._twitch_channel or "unknown"})
            if info_type == "viewer_count":
                vc = self._twitch_viewer_count
                return json.dumps({"viewer_count": vc if vc is not None else "不明"})
        return json.dumps({"error": "unknown tool"})

    def _http_post_json(self, payload: dict) -> dict:
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
                return json.loads(resp.read().decode("utf-8"))  # type: ignore[no-any-return]
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            LOGGER.warning("OpenAI HTTP %s error body: %s", exc.code, body)
            raise

    def _extract_output_text(self, response: dict) -> str:
        """Responses API レスポンスからテキストを取り出す。"""
        if isinstance(response.get("output_text"), str) and response["output_text"].strip():
            return response["output_text"]
        output = response.get("output", [])
        if output and isinstance(output, list):
            for item in output:
                if item.get("type") == "message":
                    content = item.get("content", [])
                    if content and isinstance(content, list):
                        # 全 content item のテキストを結合（分割されている場合に備えて）
                        parts = [c.get("text", "") for c in content if c.get("text")]
                        text = "".join(parts)
                        if text:
                            return text
        return ""

    def _call_openai_http_with_tools(
        self, event: ConversationInputEvent
    ) -> tuple[str, list[str]]:
        """ツールループを実行し (最終テキスト, robot_actions) を返す。"""
        robot_actions: list[str] = []
        tools = self._build_tools_list()
        payload: dict = {
            "model": self.cfg.openai_realtime_model,
            "input": self._build_prompt(event, self._history_text()),
        }
        if tools:
            payload["tools"] = tools

        fallback_json = json.dumps({"reply": FALLBACK_REPLY, "emotion": "empathy"})

        for _ in range(5):
            try:
                response = self._http_post_json(payload)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                LOGGER.warning("OpenAI HTTP request failed in tool loop: %s", exc)
                return fallback_json, robot_actions

            output = response.get("output", [])
            out_types = [o.get("type") for o in output]
            LOGGER.info("Tool loop response output types: %s", out_types)
            fn_calls = [o for o in output if o.get("type") == "function_call"]

            if not fn_calls:
                text = self._extract_output_text(response)
                LOGGER.debug("Tool loop final text: %r", text[:200] if text else "(empty)")
                if not text:
                    out_types = [o.get("type") for o in output]
                    LOGGER.warning("Tool loop: empty text from response, output types=%s", out_types)
                    # web_search_call のみで message がない場合は次ターンへ継続
                    if any(t == "web_search_call" for t in out_types):
                        payload = {
                            "model": self.cfg.openai_realtime_model,
                            "previous_response_id": response.get("id", ""),
                            "input": [],
                        }
                        continue
                return text or fallback_json, robot_actions

            tool_results = []
            for fc in fn_calls:
                fc_id = fc.get("call_id") or fc.get("id", "")
                try:
                    args = json.loads(fc.get("arguments", "{}"))
                except Exception:
                    args = {}
                result = self._execute_function_call(fc.get("name", ""), args, robot_actions)
                tool_results.append(
                    {"type": "function_call_output", "call_id": fc_id, "output": result}
                )

            payload = {
                "model": self.cfg.openai_realtime_model,
                "previous_response_id": response.get("id", ""),
                "input": tool_results,
            }

        return fallback_json, robot_actions

    def _call_openai_http(self, event: ConversationInputEvent) -> str:
        """ツールなし単発 HTTP 呼び出し（フォールバック用）。"""
        payload = {
            "model": self.cfg.openai_realtime_model,
            "input": self._build_prompt(event, self._history_text()),
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
                {"reply": FALLBACK_REPLY, "emotion": "empathy"}
            )

        try:
            parsed = json.loads(body)
            text = self._extract_output_text(parsed)
            if text:
                return text
        except Exception:
            pass

        return json.dumps({"reply": FALLBACK_REPLY, "emotion": "empathy"})


class OpenAIHttpSession(_OpenAISessionBase):
    async def generate(self, event: ConversationInputEvent) -> ConversationOutputEvent:
        if not self.cfg.openai_api_key:
            return self._fallback_output()

        try:
            response_text, robot_actions = await asyncio.to_thread(
                self._call_openai_http_with_tools, event
            )
        except Exception as exc:
            LOGGER.warning("OpenAI generation failed, fallback reply is used: %s", exc)
            return self._fallback_output()

        parsed = self._parse_response(response_text)
        safe = self._post_safety(parsed.reply_text)
        if not safe:
            return self._fallback_output()

        output = ConversationOutputEvent(
            reply_text=self._decorate_reply(event, safe),
            emotion=parsed.emotion,
            tool_calls=robot_actions,
        )
        self._append_turn(event, output)
        return output


class OpenAIRealtimeSession(_OpenAISessionBase):
    """Realtime-style serialized session.

    The worker serializes `conversation.item.create -> response.create` semantics.
    If websocket realtime transport is unavailable, it falls back to HTTP while
    keeping the same serialization and retry guarantees.
    """

    def __init__(self, cfg: ConversationConfig, safety_cfg: SafetyConfig) -> None:
        super().__init__(cfg, safety_cfg)
        self._response_lock = asyncio.Lock()

    async def generate(self, event: ConversationInputEvent) -> ConversationOutputEvent:
        if not self.cfg.openai_api_key:
            return self._fallback_output()

        try:
            async with self._response_lock:
                return await asyncio.wait_for(
                    self._generate_with_retry(event),
                    timeout=max(self.cfg.openai_timeout_sec, 1.0) + 5.0,
                )
        except (asyncio.TimeoutError, TimeoutError, Exception) as exc:
            LOGGER.warning("Realtime generation failed; fallback reply is used: %s", exc)
            return self._fallback_output()

    async def _generate_with_retry(self, event: ConversationInputEvent) -> ConversationOutputEvent:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response_text, robot_actions = await asyncio.to_thread(self._call_openai, event)
                parsed = self._parse_response(response_text)
                safe = self._post_safety(parsed.reply_text)
                if not safe:
                    return self._fallback_output()
                output = ConversationOutputEvent(
                    reply_text=self._decorate_reply(event, safe),
                    emotion=parsed.emotion,
                    tool_calls=robot_actions,
                )
                self._append_turn(event, output)
                return output
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.1 * (attempt + 1))
        if last_error is not None:
            raise last_error
        return self._fallback_output()

    def _call_openai(self, event: ConversationInputEvent) -> tuple[str, list[str]]:
        return self._call_openai_http_with_tools(event)


def create_conversation_session(
    cfg: ConversationConfig,
    safety_cfg: SafetyConfig,
) -> ConversationSession:
    if cfg.engine == "http":
        return OpenAIHttpSession(cfg, safety_cfg)
    return OpenAIRealtimeSession(cfg, safety_cfg)
