from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from .config import SafetyConfig
from .types import SafetyDecision, TwitchMessage

UNSAFE_INTENTS = ["個人情報", "住所", "電話番号", "差別", "暴力"]


@dataclass(slots=True)
class _WindowEntry:
    user_id: str
    text: str
    at: float


class SafetyFilter:
    def __init__(self, cfg: SafetyConfig) -> None:
        self.cfg = cfg
        self._recent: deque[_WindowEntry] = deque()

    def evaluate(self, msg: TwitchMessage, normalized_text: str) -> SafetyDecision:
        now = time.time()
        self._evict_old(now)

        if len(normalized_text) > self.cfg.max_chars:
            return SafetyDecision(allow=False, reason="too_long")

        low = normalized_text.lower()
        for w in self.cfg.ng_words:
            if w.lower() in low:
                return SafetyDecision(allow=False, reason="ng_word")

        for key in UNSAFE_INTENTS:
            if key in normalized_text:
                return SafetyDecision(allow=False, reason="unsafe_intent")

        if self._is_spam(msg.user_id, low):
            return SafetyDecision(allow=False, reason="spam")

        self._recent.append(_WindowEntry(user_id=msg.user_id, text=low, at=now))
        return SafetyDecision(allow=True, reason="ok", sanitized_text=normalized_text)

    def _is_spam(self, user_id: str, text: str) -> bool:
        dup = 0
        for x in self._recent:
            if x.user_id == user_id and x.text == text:
                dup += 1
        return dup >= 2

    def _evict_old(self, now: float) -> None:
        min_time = now - self.cfg.spam_window_sec
        while self._recent and self._recent[0].at < min_time:
            self._recent.popleft()
