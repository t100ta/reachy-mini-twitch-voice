from __future__ import annotations

import random
import time

from .types import ConversationOutputEvent, GesturePreset


class ToolExecutor:
    """Maps conversation emotion/tool calls to robot motion presets."""

    def __init__(self) -> None:
        self._turn = 0
        self._last: GesturePreset = "idle"
        self._rng = random.Random(time.time_ns())

    def pick_gesture(self, output: ConversationOutputEvent) -> GesturePreset:
        self._turn += 1
        tool = self._pick_from_tool_calls(output.tool_calls)
        if tool is not None:
            return self._pick_non_repeating([tool, "nod", "look", "tilt"])

        text = output.reply_text
        text_low = text.lower()

        if any(k in text for k in ["すごい", "最高", "やった", "うれしい", "楽しい"]) or any(
            k in text_low for k in ["great", "amazing", "nice", "love"]
        ):
            return self._pick_non_repeating(["sway", "nod", "tilt"])
        if any(k in text for k in ["えっ", "ほんと", "まじ", "びっくり", "なるほど"]) or any(
            k in text_low for k in ["wow", "really", "surpr", "interesting"]
        ):
            return self._pick_non_repeating(["tilt", "look", "nod"])

        if output.emotion == "joy":
            base = ["nod", "sway", "tilt", "look"] if ("!" in text or "！" in text) else ["nod", "tilt", "sway"]
            return self._pick_non_repeating(base)
        if output.emotion == "surprise":
            base = ["tilt", "look", "nod"] if ("?" in text or "？" in text) else ["look", "tilt"]
            return self._pick_non_repeating(base)
        # empathy / neutral
        base = ["nod", "tilt", "look"] if len(text) < 36 else ["nod", "look", "sway", "tilt"]
        return self._pick_non_repeating(base)

    def _pick_from_tool_calls(self, tool_calls: list[str]) -> GesturePreset | None:
        lowered = [t.lower() for t in tool_calls]
        for t in lowered:
            if "dance.short" in t or "dance" in t:
                return "sway"
            if "move_head.left" in t or "move.left" in t:
                return "look"
            if "move_head.right" in t or "move.right" in t:
                return "look"
            if "move_head.up" in t or "move.up" in t:
                return "nod"
            if "move_head.down" in t or "move.down" in t:
                return "nod"
            if "tilt" in t or "emotion" in t:
                return "tilt"
            if "settle" in t or "idle" in t:
                return "idle"
        return None

    def _pick_non_repeating(self, candidates: list[GesturePreset]) -> GesturePreset:
        if not candidates:
            return "nod"
        idx = self._rng.randrange(0, len(candidates))
        ordered = candidates[idx:] + candidates[:idx]
        for g in ordered:
            if g != self._last:
                self._last = g
                return g
        self._last = ordered[0]
        return ordered[0]
