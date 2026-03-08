from __future__ import annotations

import re

URL_PATTERN = re.compile(r"https?://\S+")
WHITESPACE_PATTERN = re.compile(r"\s+")
REPEAT_PATTERN = re.compile(r"(.)\1{4,}")


def normalize_comment(text: str) -> str:
    text = text.strip()
    text = URL_PATTERN.sub("[link]", text)
    text = REPEAT_PATTERN.sub(r"\1\1\1", text)
    text = WHITESPACE_PATTERN.sub(" ", text)
    return text
