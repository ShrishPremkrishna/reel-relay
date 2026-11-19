from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import urlparse

from .credentials import Vault
from .store import Store
from .util import APIError, RelayError, http_json


def token_path(cfg, service):
    return Path(cfg["data_dir"]) / "secrets" / f"{service}.json"


def twitch_device_start(cfg, client_id, *, request=http_json):
    if not client_id or not client_id.isalnum():
        raise RelayError("A registered Twitch application's client ID is required")
    device = request(
        "https://id.twitch.tv/oauth2/device",
        method="POST",
        form={"client_id": client_id, "scopes": "user:read:chat"},
    )
    destination = urlparse(device["verification_uri"])
    if destination.scheme != "https" or destination.hostname not in {
        "www.twitch.tv",
        "twitch.tv",
        "id.twitch.tv",
    }:
        raise RelayError("Unexpected Twitch verification destination")
    interval = max(float(device.get("interval", 5)), 5)
    Vault(cfg).put(
        "twitch-device",
        {
            **device,
            "client_id": client_id,
            "expires_at": time.time() + min(float(device["expires_in"]), 1800),
            "interval": interval,
            "next_poll_at": time.time() + interval,
        },
    )
    return {
        "status": "pending",
        "user_code": device["user_code"],
        "verification_uri": device["verification_uri"],
        "interval": interval,
        "expires_in": device["expires_in"],
    }


def twitch_device_poll(cfg, *, request=http_json):
    vault = Vault(cfg)
    with Store(cfg).lock("twitch-device-poll", seconds=60):
        device = vault.get("twitch-device")
        if not device or device["expires_at"] <= time.time():
            vault.remove("twitch-device")
            raise RelayError("Twitch sign-in expired; start again")
        if time.time() < device["next_poll_at"]:
            return {"status": "pending", "interval": device["interval"]}
        try:
            token = request(
                "https://id.twitch.tv/oauth2/token",
                method="POST",
                form={
                    "client_id": device["client_id"],
                    "device_code": device["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
        except APIError as e:
            message = str(e.payload.get("message", e.payload.get("error", ""))).lower().replace(" ", "_")
            if message == "slow_down":
                device["interval"] += 5
            if message in ("authorization_pending", "slow_down"):
                device["next_poll_at"] = time.time() + device["interval"]
                vault.put("twitch-device", device)
                return {"status": "pending", "interval": device["interval"]}
            vault.remove("twitch-device")
            raise
        token["client_id"] = device["client_id"]
        token["obtained_at"] = time.time()
        identity = request(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": "OAuth " + token["access_token"]},
        )
        if identity.get("client_id") != device["client_id"] or "user:read:chat" not in identity.get(
            "scopes", []
        ):
            raise RelayError("Twitch authorization did not include chat read access")
        token.update(user_id=identity["user_id"], login=identity["login"])
        token.update(expires_at=time.time() + token.get("expires_in", 0), scopes=identity.get("scopes", []))
        vault.put("twitch", token)
        vault.remove("twitch-device")
        return {"status": "connected", "username": identity["login"]}


def twitch_login(cfg, client_id, *, announce=print, stop=None):
    device = twitch_device_start(cfg, client_id)
    announce(f"Open {device['verification_uri']} and enter code {device['user_code']}")
    interval = device["interval"]
    while True:
        if stop and stop.wait(interval):
            return None
        if not stop:
            time.sleep(interval)
        result = twitch_device_poll(cfg)
        if result["status"] == "connected":
            announce(
                f"Connected Twitch as {result['username']}. Credentials saved in the encrypted local vault."
            )
            return result["username"]
        interval = result["interval"]


def twitch_token(cfg):
    vault = Vault(cfg)
    token = vault.get("twitch")
    if not token:
        raise RelayError("Connect Twitch with: ./relay auth twitch --client-id YOUR_APP_CLIENT_ID")
    try:
        identity = http_json(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": "OAuth " + token["access_token"]},
        )
    except APIError as e:
        if e.status != 401 or not token.get("refresh_token"):
            raise
        renewed = http_json(
            "https://id.twitch.tv/oauth2/token",
            method="POST",
            form={
                "client_id": token["client_id"],
                "refresh_token": token["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        token.update(renewed, obtained_at=time.time())
        identity = http_json(
            "https://id.twitch.tv/oauth2/validate",
            headers={"Authorization": "OAuth " + token["access_token"]},
        )
        token["expires_at"] = time.time() + token.get("expires_in", 0)
        vault.put("twitch", token)
    if identity.get("client_id") != token["client_id"] or "user:read:chat" not in identity.get("scopes", []):
        raise RelayError("Twitch token belongs to another app or lacks chat permission")
    token.update(user_id=identity["user_id"], login=identity["login"])
    return token


def instagram_import(cfg, source):
    # Use a file/getpass, never a token in a shell argument or model conversation.
    token = Path(source).read_text().strip()
    if not token or "\n" in token:
        raise RelayError("Token file must contain one token")
    version = cfg["publish"]["api_version"]
    info = http_json(
        f"https://graph.instagram.com/{version}/me?fields=user_id,username",
        headers={"Authorization": "Bearer " + token},
    )
    account_id = str(info.get("user_id") or info.get("id") or "")
    if not account_id.isdigit() or not info.get("username"):
        raise RelayError("Could not verify this Instagram professional account")
    Vault(cfg).put(
        "instagram",
        {
            "access_token": token,
            "account_id": account_id,
            "username": info["username"],
            "obtained_at": time.time(),
            "long_lived": False,
            "scopes": [],
            "expiry_unknown": True,
        },
    )
    return {"username": info["username"], "account_id": account_id}
