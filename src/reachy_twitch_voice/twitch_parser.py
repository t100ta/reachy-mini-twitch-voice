from __future__ import annotations

import time
import uuid

from .types import TwitchMessage


def parse_privmsg(raw: str) -> TwitchMessage | None:
    # Example:
    # :user!user@user.tmi.twitch.tv PRIVMSG #channel :hello
    if " PRIVMSG #" not in raw:
        return None

    try:
        prefix, trailing = raw.split(" PRIVMSG #", 1)
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
        received_at=time.time(),
    )
