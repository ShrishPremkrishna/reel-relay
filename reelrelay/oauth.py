"""Official Instagram authorization-code flow and single-use browser-bound states."""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlparse

import sqlalchemy as sa

from . import db
from .credentials import Vault
from .store import Store
from .util import RelayError, http_json

INSTAGRAM_SCOPES = {"instagram_business_basic", "instagram_business_content_publish"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class OAuthStates:
    def __init__(self, cfg):
        self.store = Store(cfg)

    def begin(self, provider, binding, payload=None, *, ttl=600):
        state = secrets.token_urlsafe(32)
        body = {**(payload or {}), "binding_hash": digest(binding)}
        with self.store.connect(write=True) as connection:
            connection.execute(db.oauth_flows.delete().where(db.oauth_flows.c.expires_at < time.time()))
            connection.execute(
                db.oauth_flows.insert().values(
                    state_hash=digest(state),
                    provider=provider,
                    expires_at=time.time() + ttl,
                    payload=json.dumps(body),
                )
            )
        return state

    def consume(self, provider, state, binding):
        if not state or not binding:
            raise RelayError("Sign-in state is missing. Start the connection again from this browser")
        query = sa.select(db.oauth_flows).where(
            db.oauth_flows.c.state_hash == digest(state),
            db.oauth_flows.c.provider == provider,
            db.oauth_flows.c.expires_at > time.time(),
        )
        with self.store.connect(write=True) as connection:
            record = connection.execute(self.store.row_lock(query)).mappings().first()
            if not record:
                raise RelayError("Sign-in link expired or was already used; start the connection again")
            payload = json.loads(record["payload"])
            if not secrets.compare_digest(payload["binding_hash"], digest(binding)):
                raise RelayError("Sign-in belongs to a different browser session")
            connection.execute(db.oauth_flows.delete().where(db.oauth_flows.c.state_hash == digest(state)))
        return payload


def instagram_start(cfg, binding):
    app_id, redirect = cfg["auth"]["instagram_app_id"], cfg["auth"]["instagram_redirect_uri"]
    if not app_id.isdigit() or not Vault(cfg).exists("instagram-app"):
        raise RelayError("Add your Instagram app ID and app secret in Setup before connecting the account")
    parsed = urlparse(redirect)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise RelayError("Set the exact HTTPS callback URL registered in your Meta app")
    state = OAuthStates(cfg).begin("instagram", binding, {"redirect_uri": redirect, "app_id": app_id})
    return "https://www.instagram.com/oauth/authorize?" + urlencode(
        {
            "client_id": app_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": ",".join(sorted(INSTAGRAM_SCOPES)),
            "state": state,
            "enable_fb_login": "false",
            "force_reauth": "true",
        }
    )


def instagram_callback(cfg, *, state, binding, code=None, error=None, request=http_json):
    flow = OAuthStates(cfg).consume("instagram", state, binding)
    if error:
        raise RelayError("Instagram connection was canceled. Your accounts were not changed")
    if not code:
        raise RelayError("Instagram did not return an authorization code")
    vault = Vault(cfg)
    secret = vault.get("instagram-app")
    if not secret:
        raise RelayError("Instagram app secret is missing")
    response = request(
        "https://api.instagram.com/oauth/access_token",
        method="POST",
        form={
            "client_id": flow["app_id"],
            "client_secret": secret["client_secret"],
            "grant_type": "authorization_code",
            "redirect_uri": flow["redirect_uri"],
            "code": code.removesuffix("#_"),
        },
    )
    short = response.get("data", [response])[0]
    scopes = short.get("permissions", [])
    if isinstance(scopes, str):
        scopes = [s.strip() for s in scopes.split(",")]
    if not INSTAGRAM_SCOPES <= set(scopes):
        raise RelayError("Instagram did not grant both profile and content-publishing permissions")
    long = request(
        "https://graph.instagram.com/access_token?"
        + urlencode(
            {
                "grant_type": "ig_exchange_token",
                "client_secret": secret["client_secret"],
                "access_token": short["access_token"],
            }
        )
    )
    identity = request(
        "https://graph.instagram.com/" + cfg["publish"]["api_version"] + "/me?fields=user_id,username",
        headers={"Authorization": "Bearer " + long["access_token"]},
    )
    account_id = str(identity.get("user_id") or identity.get("id") or "")
    if not account_id.isdigit() or account_id != str(short["user_id"]) or not identity.get("username"):
        raise RelayError("Instagram account identity could not be verified")
    now = time.time()
    record = {
        "access_token": long["access_token"],
        "account_id": account_id,
        "username": identity["username"],
        "scopes": sorted(scopes),
        "obtained_at": now,
        "expires_at": now + long["expires_in"],
        "long_lived": True,
    }
    vault.put("instagram", record)
    Store(cfg).event("account_connected", {"service": "instagram", "username": identity["username"]})
    return vault.account_status("instagram")


def instagram_token(cfg, *, request=http_json, force_refresh=False):
    vault = Vault(cfg)
    token = vault.get("instagram")
    if not token:
        raise RelayError("Connect the dedicated Instagram professional account before publishing")
    now = time.time()
    if token.get("expires_at", now + 1) <= now:
        raise RelayError("Instagram sign-in expired; reconnect the account in Setup")
    needs_refresh = token.get("long_lived") and (
        force_refresh or token.get("expires_at", now) - now < 7 * 86400
    )
    if needs_refresh and now - token["obtained_at"] >= 86400:
        with Store(cfg).lock("instagram-token-refresh", seconds=120):
            token = vault.get("instagram")
            if now - token["obtained_at"] >= 86400:
                result = request(
                    "https://graph.instagram.com/refresh_access_token?"
                    + urlencode({"grant_type": "ig_refresh_token", "access_token": token["access_token"]})
                )
                token.update(
                    access_token=result["access_token"],
                    obtained_at=now,
                    expires_at=now + result["expires_in"],
                )
                vault.put("instagram", token)
    return token
