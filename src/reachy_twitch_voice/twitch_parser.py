from __future__ import annotations

import time
import uuid

from .types import TwitchMessage


def parse_privmsg(raw: str) -> TwitchMessage | None:
    # Example:
    # :user!user@user.tmi.twitch.tv PRIVMSG #channel :hello
    if " PRIVMSG #" not in raw:
        return None

    received_at = time.time()
    line = raw
    if raw.startswith("@") and " :" in raw:
        try:
            tags, line = raw.split(" ", 1)
            for kv in tags.lstrip("@").split(";"):
                if kv.startswith("tmi-sent-ts="):
                    ms = int(kv.split("=", 1)[1])
                    if ms > 0:
                        received_at = ms / 1000.0
                    break
        except Exception:
            line = raw

    try:
        prefix, trailing = line.split(" PRIVMSG #", 1)
        channel, text = trailing.split(" :", 1)
        user = prefix.split("!", 1)[0].lstrip(":")
    except ValueError:
        return None

    text = text.strip("\r\n")
    if not user or not channel or not text:
        return None

    return TwitchMessage(
        id=str(uuid.uuid4()),
        channel=channel.strip().lower(),
        user_id=user.lower(),
        user_name=user,
        text=text,
        received_at=received_at,
    )
