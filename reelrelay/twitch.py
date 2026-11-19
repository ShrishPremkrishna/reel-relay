from __future__ import annotations

import hashlib
import json
import random
import socket
import time
from urllib.parse import urlparse

from .auth import twitch_token
from .detector import ChatMessage
from .util import RelayError, http_json, tls_context


def parse_irc(line, received_at=None):
    tags = {}
    if line.startswith("@"):
        raw, line = line.split(" ", 1)
        tags = dict(p.split("=", 1) if "=" in p else (p, "") for p in raw[1:].split(";"))
    if " PRIVMSG #" not in line or " :" not in line:
        return None
    prefix, text = line.split(" :", 1)
    user = prefix.split("!", 1)[0].lstrip(":")
    channel = prefix.split(" PRIVMSG #", 1)[1].split()[0]
    now = received_at if received_at is not None else time.time()
    mid = tags.get("id") or hashlib.sha256(f"{now}:{line}".encode()).hexdigest()
    sent = float(tags["tmi-sent-ts"]) / 1000 if tags.get("tmi-sent-ts", "").isdigit() else None
    return channel, ChatMessage(now, user, text, mid, sent)


def anonymous_chat(channels, callback, stop, health):
    """Experimental, read-only public IRC. No website cookie or account token."""
    backoff = 2
    while not stop.is_set():
        try:
            with (
                socket.create_connection(("irc.chat.twitch.tv", 6697), timeout=10) as raw,
                tls_context().wrap_socket(raw, server_hostname="irc.chat.twitch.tv") as sock,
            ):
                sock.settimeout(1)
                nickname = f"justinfan{random.randint(10000, 999999)}"
                sock.sendall(
                    (
                        f"PASS SCHMOOPIIE\r\nNICK {nickname}\r\n"
                        "CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
                        + "".join(f"JOIN #{c}\r\n" for c in channels)
                    ).encode()
                )
                buffer, backoff, last_data = b"", 2, time.monotonic()
                for c in channels:
                    health(
                        c,
                        chat_state="connected; waiting for messages",
                        chat_transport="anonymous-irc",
                        chat_error=None,
                    )
                while not stop.is_set():
                    try:
                        chunk = sock.recv(65536)
                    except TimeoutError:
                        if time.monotonic() - last_data > 330:
                            raise RelayError("Chat connection stopped responding")
                        continue
                    if not chunk:
                        raise RelayError("Chat connection closed")
                    last_data = time.monotonic()
                    buffer += chunk
                    if len(buffer) > 2 * 1024 * 1024:
                        raise RelayError("Oversized chat frame")
                    while b"\r\n" in buffer:
                        raw_line, buffer = buffer.split(b"\r\n", 1)
                        line = raw_line.decode("utf-8", "replace")
                        if line.startswith("PING "):
                            sock.sendall(("PONG " + line[5:] + "\r\n").encode())
                        elif "Login authentication failed" in line:
                            raise RelayError("Anonymous chat was refused; use authenticated EventSub")
                        parsed = parse_irc(line)
                        if parsed and parsed[0] in channels:
                            callback(*parsed)
        except (OSError, RelayError) as exc:
            for c in channels:
                health(c, chat_state="reconnecting", chat_error=str(exc)[:200])
            stop.wait(backoff)
            backoff = min(backoff * 2, 60)


def eventsub_chat(cfg, channels, callback, stop, health):
    try:
        import websocket
    except ImportError:
        raise RelayError("Install the 'live' dependencies for EventSub") from None
    base = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
    reconnect_url, old_socket = None, None
    while not stop.is_set():
        ws = None
        try:
            token = twitch_token(cfg)
            headers = {"Authorization": "Bearer " + token["access_token"], "Client-Id": token["client_id"]}
            query = "&".join("login=" + c for c in channels)
            users = http_json("https://api.twitch.tv/helix/users?" + query, headers=headers)["data"]
            ids = {u["login"]: u["id"] for u in users}
            if set(ids) != set(channels):
                raise RelayError("One or more configured Twitch channels do not exist")
            migrating = reconnect_url is not None
            ws = websocket.create_connection(
                reconnect_url or base, timeout=10, sslopt={"context": tls_context()}
            )
            welcome = json.loads(ws.recv())
            if welcome.get("metadata", {}).get("message_type") != "session_welcome":
                raise RelayError("Twitch did not send a session welcome")
            session = welcome["payload"]["session"]
            if not migrating:
                for name, broadcaster_id in ids.items():
                    http_json(
                        "https://api.twitch.tv/helix/eventsub/subscriptions",
                        method="POST",
                        headers=headers,
                        data={
                            "type": "channel.chat.message",
                            "version": "1",
                            "condition": {"broadcaster_user_id": broadcaster_id, "user_id": token["user_id"]},
                            "transport": {"method": "websocket", "session_id": session["id"]},
                        },
                    )
                    health(
                        name,
                        chat_state="connected; waiting for messages",
                        chat_transport="eventsub",
                        chat_error=None,
                    )
            if old_socket:
                old_socket.close()
                old_socket = None
            reconnect_url = None
            ws.settimeout(1)
            last_received, validated_at = time.monotonic(), time.monotonic()
            keepalive = session.get("keepalive_timeout_seconds") or 30
            while not stop.is_set():
                if time.monotonic() - validated_at > 3500:
                    # A fresh session subscribes using a validated/refreshed user token.
                    break
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    if time.monotonic() - last_received > keepalive + 5:
                        raise RelayError("Twitch keepalive timed out")
                    continue
                if not raw:
                    raise RelayError("Twitch EventSub disconnected")
                packet = json.loads(raw)
                last_received = time.monotonic()
                kind = packet["metadata"]["message_type"]
                if (
                    kind == "notification"
                    and packet["payload"]["subscription"]["type"] == "channel.chat.message"
                ):
                    ev = packet["payload"]["event"]
                    callback(
                        ev["broadcaster_user_login"],
                        ChatMessage(
                            time.time(), ev["chatter_user_login"], ev["message"]["text"], ev["message_id"]
                        ),
                    )
                elif kind == "session_reconnect":
                    target = packet["payload"]["session"]["reconnect_url"]
                    parsed = urlparse(target)
                    if parsed.scheme != "wss" or parsed.hostname != "eventsub.wss.twitch.tv":
                        raise RelayError("Unexpected Twitch reconnect destination")
                    reconnect_url, old_socket, ws = target, ws, None
                    break
                elif kind == "revocation":
                    raise RelayError("Twitch revoked a chat subscription; reconnect your account")
        except Exception as exc:  # noqa: BLE001 -- reconnect this channel after transport/protocol failures
            reconnect_url = None
            if old_socket:
                old_socket.close()
                old_socket = None
            for name in channels:
                health(name, chat_state="error", chat_error=str(exc)[:200])
            stop.wait(10)
        finally:
            if ws:
                ws.close()
    if old_socket:
        old_socket.close()
