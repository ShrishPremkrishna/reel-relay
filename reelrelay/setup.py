"""Versioned, UI-facing settings and write-only credential management."""

from __future__ import annotations

import hashlib
import os
import uuid
from copy import copy
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, create_model

from .config import (
    Agent,
    Capture,
    Channel,
    ConfigurationConflict,
    Detector,
    Editing,
    Media,
    Perception,
    StrictModel,
    load_versioned,
    mutate,
)
from .content_flags import CONTENT_FLAGS, KNOWN_FLAGS
from .credentials import Vault
from .store import Store
from .util import RelayError


def _patch_model(model, excluded=()):
    fields = {}
    for name, field in model.model_fields.items():
        if name in excluded:
            continue
        field = copy(field)
        field.default, field.default_factory = None, None
        fields[name] = (field.annotation, field)
    # Cross-field validators run against the merged Settings, not patch defaults.
    return create_model(model.__name__ + "Patch", __base__=StrictModel, **fields)


CapturePatch = _patch_model(Capture)
DetectorPatch = _patch_model(Detector)
AgentPatch = _patch_model(Agent, ("provider", "model", "selection_mode"))
EditingPatch = _patch_model(Editing)
MediaPatch = _patch_model(Media)
PerceptionPatch = _patch_model(Perception, ("provider", "model", "allow_cloud_upload"))


class Delivery(StrictModel):
    public_base_url: str = Field("", max_length=2000)
    public_media_base: str = Field("", max_length=2000)
    s3_bucket: str = Field("", max_length=200)
    s3_endpoint: str = Field("", max_length=2000)
    s3_region: str = Field("auto", max_length=100)
    api_version: str = Field("v24.0", pattern=r"^v\d+\.\d+$")


class Setup(StrictModel):
    config_version: str | None = Field(None, max_length=64)
    channels: list[Channel] | None = Field(None, max_length=64)
    provider: Literal["manual", "command", "ollama", "codex", "gemini"] | None = None
    model: str | None = Field(None, max_length=200)
    selection_mode: Literal["balanced", "throughput"] | None = None
    instagram_app_id: str | None = Field(None, pattern=r"^\d*$", max_length=64)
    instagram_redirect_uri: str | None = Field(None, max_length=2000)
    instagram_app_secret: SecretStr | None = Field(None, max_length=2000)
    twitch_client_id: str | None = Field(None, pattern=r"^[a-zA-Z0-9]*$", max_length=128)
    max_per_day: int | None = Field(None, ge=1, le=100)
    min_interval_seconds: int | None = Field(None, ge=0, le=86400)
    perception_provider: Literal["none", "ollama", "gemini", "command"] | None = None
    perception_model: str | None = Field(None, max_length=200)
    allow_cloud_upload: bool | None = None
    gemini_api_key: SecretStr | None = Field(None, max_length=2000)
    s3_access_key_id: SecretStr | None = Field(None, max_length=2000)
    s3_secret_access_key: SecretStr | None = Field(None, max_length=2000)
    clear_gemini_key: bool = False
    clear_s3_credentials: bool = False
    clear_instagram_app_secret: bool = False
    capture: CapturePatch | None = None
    detector: DetectorPatch | None = None
    agent_controls: AgentPatch | None = None
    editing: EditingPatch | None = None
    media: MediaPatch | None = None
    perception_controls: PerceptionPatch | None = None
    delivery: Delivery | None = None


def credential_sources(vault):
    return {
        "gemini": "environment"
        if os.environ.get("GEMINI_API_KEY")
        else "saved"
        if vault.exists("gemini")
        else "none",
        "s3": "environment"
        if any(
            os.environ.get(key) for key in ("REEL_RELAY_S3_ACCESS_KEY_ID", "REEL_RELAY_S3_SECRET_ACCESS_KEY")
        )
        else "saved"
        if vault.exists("media-host")
        else "none",
        "instagram_app_secret": "saved" if vault.exists("instagram-app") else "none",
    }


def _settings_version(config_version, store):
    revision = store.get_setting("setup_revision", "")
    return hashlib.sha256(f"{config_version}:{revision}".encode()).hexdigest()


def setup_snapshot(cfg, csrf):
    version = None
    if cfg.get("config_path"):
        cfg, version = load_versioned(cfg["config_path"])
        version = _settings_version(version, Store(cfg))
    vault = Vault(cfg)
    sources = credential_sources(vault)
    return {
        "csrf": csrf,
        "config_version": version,
        "channels": cfg["channels"],
        **{key: cfg["agent"][key] for key in ("provider", "model", "selection_mode")},
        "perception": {
            **{key: cfg["perception"][key] for key in ("provider", "model", "allow_cloud_upload")},
            "api_key_saved": sources["gemini"] != "none",
        },
        "auth": cfg["auth"],
        "instagram_app_secret_saved": vault.exists("instagram-app"),
        "s3_credentials_saved": sources["s3"] != "none",
        "credential_sources": sources,
        "accounts": {name: vault.account_status(name) for name in ("twitch", "instagram")},
        "publishing": {
            key: cfg["publish"][key]
            for key in (
                "enabled",
                "dry_run",
                "auto_approve",
                "expected_username",
                "max_per_day",
                "min_interval_seconds",
            )
        },
        "public_base_url": cfg["web"]["public_base_url"],
        "media_host_configured": bool(
            cfg["web"]["public_base_url"]
            or (cfg["publish"]["public_media_base"] and cfg["publish"]["s3_bucket"])
        ),
        "capture": cfg["capture"],
        "detector": cfg["detector"],
        "editing": cfg["editing"],
        "media": cfg["media"],
        "agent_controls": {
            key: value
            for key, value in cfg["agent"].items()
            if key not in ("provider", "model", "selection_mode")
        },
        "perception_controls": {
            key: value
            for key, value in cfg["perception"].items()
            if key not in ("provider", "model", "allow_cloud_upload")
        },
        "delivery": {
            "public_base_url": cfg["web"]["public_base_url"],
            **{key: cfg["publish"][key] for key in Delivery.model_fields if key != "public_base_url"},
        },
        "content_flags": CONTENT_FLAGS,
    }


def save_setup(cfg, body):
    if not cfg.get("config_path"):
        raise RelayError("This server needs a saved workspace configuration")
    vault = Vault(cfg)
    sources = credential_sources(vault)
    changes = []
    for name, source, clear, values in (
        ("gemini", "gemini", body.clear_gemini_key, {"api_key": body.gemini_api_key}),
        (
            "media-host",
            "s3",
            body.clear_s3_credentials,
            {"access_key_id": body.s3_access_key_id, "secret_access_key": body.s3_secret_access_key},
        ),
        (
            "instagram-app",
            "instagram_app_secret",
            body.clear_instagram_app_secret,
            {"client_secret": body.instagram_app_secret},
        ),
    ):
        secrets = {
            key: value.get_secret_value().strip()
            for key, value in values.items()
            if value and value.get_secret_value().strip()
        }
        if (clear or secrets) and sources[source] == "environment":
            raise RelayError("This credential is managed by the server environment; change it there first")
        if clear and secrets:
            raise RelayError("Choose either replacing or clearing a credential")
        if secrets and len(secrets) != len(values):
            raise RelayError("Enter both the media-host access key and secret key")
        if clear or secrets:
            changes.append((name, secrets or None))
    if (
        body.agent_controls
        and "allowed_flags" in body.agent_controls.model_fields_set
        and set(body.agent_controls.allowed_flags) - KNOWN_FLAGS
    ):
        raise RelayError("Only recognized content flags can be allowed; unknown flags remain held")

    before_origin = cfg["web"]["public_base_url"]

    def change(current):
        if body.channels is not None:
            current["channels"] = [channel.model_dump() for channel in body.channels]
        for section, attributes in (
            ("agent", ("provider", "model", "selection_mode")),
            ("auth", ("instagram_app_id", "instagram_redirect_uri", "twitch_client_id")),
            ("publish", ("max_per_day", "min_interval_seconds")),
        ):
            current[section].update(
                {key: getattr(body, key) for key in attributes if getattr(body, key) is not None}
            )
        for key, value in (
            ("provider", body.perception_provider),
            ("model", body.perception_model),
            ("allow_cloud_upload", body.allow_cloud_upload),
        ):
            if value is not None:
                current["perception"][key] = value
        for name, section in (
            ("capture", "capture"),
            ("detector", "detector"),
            ("agent_controls", "agent"),
            ("editing", "editing"),
            ("media", "media"),
            ("perception_controls", "perception"),
        ):
            value = getattr(body, name)
            if value is not None:
                current[section].update(value.model_dump(exclude_unset=True))
        if body.delivery is not None:
            for key, value in body.delivery.model_dump(exclude_unset=True).items():
                current["web" if key == "public_base_url" else "publish"][key] = value
        if current["web"]["public_base_url"] and len(os.environ.get("REEL_RELAY_WEB_TOKEN", "")) < 32:
            raise RelayError(
                "Set a strong REEL_RELAY_WEB_TOKEN in the server environment before enabling a public workspace origin. You can use S3 delivery without exposing this UI."
            )

    store = Store(cfg)
    with store.lock("ui-settings", seconds=30):
        latest, config_version = load_versioned(cfg["config_path"])
        if body.config_version is not None and body.config_version != _settings_version(
            config_version, store
        ):
            raise ConfigurationConflict("Configuration changed; reload the latest settings before saving")
        before_origin = latest["web"]["public_base_url"]
        updated = mutate(Path(cfg["config_path"]), change, expected_version=config_version)
        # Invalidate stale browser forms even when only a write-only secret changed.
        store.set_setting("setup_revision", uuid.uuid4().hex)
        for name, value in changes:
            vault.remove(name) if value is None else vault.put(name, value)
    restart = updated["web"]["public_base_url"] != before_origin
    return updated, {
        "ok": True,
        "restart_required": restart,
        "message": "Saved. Restart the server to apply its public origin and access policy."
        if restart
        else "Saved. Running workers apply changes to the next job; capture settings reload automatically.",
    }
