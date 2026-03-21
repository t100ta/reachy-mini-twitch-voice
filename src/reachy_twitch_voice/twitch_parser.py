from __future__ import annotations

import time
import uuid

from .types import ChannelEvent, TwitchMessage

_SUPPORTED_EVENT_TYPES = {"raid", "sub", "resub", "subgift", "submysterygift"}


def parse_privmsg(raw: str) -> TwitchMessage | None:
    # Example:
    # :user!user@user.tmi.twitch.tv PRIVMSG #channel :hello
    if " PRIVMSG #" not in raw:
        return None

    received_at = time.time()
    display_name: str | None = None
    line = raw
    if raw.startswith("@") and " :" in raw:
        try:
            tags, line = raw.split(" ", 1)
            for kv in tags.lstrip("@").split(";"):
                if kv.startswith("tmi-sent-ts="):
                    ms = int(kv.split("=", 1)[1])
                    if ms > 0:
                        received_at = ms / 1000.0
                elif kv.startswith("display-name="):
                    value = kv.split("=", 1)[1]
                    display_name = value or None
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
        display_name=display_name,
        text=text,
        received_at=received_at,
    )


def parse_usernotice(raw: str) -> ChannelEvent | None:
    if " USERNOTICE #" not in raw:
        return None

    received_at = time.time()
    event_type: str | None = None
    login: str | None = None
    display_name: str | None = None
    system_msg: str | None = None
    viewer_count: int | None = None
    channel: str = ""

    line = raw
    if raw.startswith("@") and " :" in raw:
        try:
            tags_str, line = raw.split(" ", 1)
            for kv in tags_str.lstrip("@").split(";"):
                key, _, value = kv.partition("=")
                if key == "msg-id":
                    event_type = value or None
                elif key == "login":
                    login = value or None
                elif key == "display-name":
                    display_name = value or None
                elif key == "system-msg":
                    system_msg = value.replace("\\s", " ") if value else None
                elif key == "msg-param-viewerCount":
                    try:
                        viewer_count = int(value)
                    except (ValueError, TypeError):
                        pass
                elif key == "tmi-sent-ts":
                    try:
                        ms = int(value)
                        if ms > 0:
                            received_at = ms / 1000.0
                    except (ValueError, TypeError):
                        pass
        except Exception:
            line = raw

    if event_type not in _SUPPORTED_EVENT_TYPES:
        return None

    try:
        _, rest = line.split(" USERNOTICE #", 1)
        channel = rest.split(" ")[0].strip().lower()
    except ValueError:
        return None

    if not channel:
        return None

    user_name = login or "unknown"

    return ChannelEvent(
        id=str(uuid.uuid4()),
        event_type=event_type,  # type: ignore[arg-type]
        channel=channel,
        user_name=user_name,
        display_name=display_name,
        system_msg=system_msg,
        viewer_count=viewer_count,
        received_at=received_at,
    )
