from __future__ import annotations

import hashlib
import os
import re
import tempfile
import tomllib
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .content_flags import CONTENT_FLAGS
from .util import RelayError


class ConfigurationConflict(RelayError):
    """A versioned settings writer used a stale snapshot."""


def channel_name(value: str) -> str:
    value = value.lower().strip()
    if not re.fullmatch(r"[a-z0-9_]{1,25}", value):
        raise ValueError("Use a Twitch channel name, without a URL")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True, hide_input_in_errors=True)


class Capture(StrictModel):
    quality: str = "720p60,720p,best"
    segment_seconds: int = Field(4, ge=1, le=15)
    retention_seconds: int = Field(300, ge=60, le=3600)
    pre_seconds: int = Field(50, ge=0, le=120)
    post_seconds: int = Field(25, ge=1, le=120)
    max_pending: int = Field(20, ge=1, le=500)
    chat_video_offset_seconds: float = Field(0, ge=-120, le=120)
    reconnect_seconds: int = Field(15, ge=1, le=300)
    max_channels: int = Field(8, ge=1, le=64)
    min_free_gb: float = Field(2, ge=0.1, le=1000)

    @model_validator(mode="after")
    def window_fits(self):
        if self.retention_seconds < self.pre_seconds + self.post_seconds + 30:
            raise ValueError("Retention must exceed the candidate window by at least 30 seconds")
        return self


class Detector(StrictModel):
    window_seconds: int = Field(10, ge=2, le=60)
    baseline_windows: int = Field(30, ge=2, le=120)
    warmup_windows: int = Field(6, ge=2, le=120)
    ratio: float = Field(3, ge=1.2, le=20)
    min_messages: int = Field(12, ge=2, le=100000)
    min_users: int = Field(6, ge=2, le=100000)
    per_user_cap: int = Field(3, ge=1, le=20)
    cooldown_seconds: int = Field(60, ge=5, le=3600)
    ignored_users: list[str] = Field(
        default_factory=lambda: ["nightbot", "streamelements", "streamlabs", "moobot"]
    )

    @model_validator(mode="after")
    def valid_warmup(self):
        if self.warmup_windows > self.baseline_windows:
            raise ValueError("Warmup cannot exceed the baseline history")
        return self


class Agent(StrictModel):
    provider: Literal["manual", "command", "ollama", "codex", "gemini"] = "manual"
    allow_cloud_upload: bool = False
    selection_mode: Literal["balanced", "throughput"] = "balanced"
    command: list[str] = Field(default_factory=list)
    model: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_context_tokens: int = Field(16384, ge=4096, le=131072)
    ollama_output_tokens: int = Field(4096, ge=512, le=16384)
    timeout_seconds: int = Field(240, ge=15, le=3600)
    min_score: float = Field(7, ge=0, le=10)
    min_confidence: float = Field(0.75, ge=0, le=1)
    max_jobs_per_hour: int = Field(12, ge=1, le=1000)
    max_revisions: int = Field(2, ge=0, le=5)
    max_retries: int = Field(3, ge=0, le=10)
    allowed_flags: list[str] = Field(
        default_factory=lambda: [row["id"] for row in CONTENT_FLAGS if row["default_action"] == "allow"],
        max_length=64,
    )


class Editing(StrictModel):
    min_seconds: int = Field(5, ge=5, le=90)
    max_seconds: int = Field(60, ge=5, le=90)
    captions_enabled: bool = True
    caption_words: int = Field(3, ge=1, le=6)
    layout: Literal["auto", "contain", "focus", "split"] = "auto"
    sfx_enabled: bool = True
    overlays_enabled: bool = True
    max_sfx: int = Field(3, ge=0, le=12)
    max_overlays: int = Field(2, ge=0, le=8)
    sfx_gain_db: float = Field(-20, ge=-36, le=-6)
    style_notes: str = Field("", max_length=2000)

    @model_validator(mode="after")
    def duration_order(self):
        if self.min_seconds > self.max_seconds:
            raise ValueError("Minimum finished length cannot exceed maximum finished length")
        return self


class Publish(StrictModel):
    enabled: bool = False
    dry_run: bool = True
    auto_approve: bool = False
    expected_username: str = ""
    max_per_day: int = Field(24, ge=1, le=100)
    min_interval_seconds: int = Field(1800, ge=0, le=86400)
    api_version: str = Field("v24.0", pattern=r"^v\d+\.\d+$")
    public_media_base: str = ""
    s3_bucket: str = ""
    s3_endpoint: str = ""
    s3_region: str = "auto"

    @field_validator("public_media_base", "s3_endpoint")
    @classmethod
    def delivery_url(cls, value, info):
        if not value:
            return value
        parsed = urlparse(value)
        local_endpoint = info.field_name == "s3_endpoint" and parsed.hostname in {
            "localhost",
            "127.0.0.1",
            "::1",
        }
        if (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or (parsed.scheme != "https" and not (local_endpoint and parsed.scheme == "http"))
        ):
            raise ValueError(
                "Use an HTTPS URL without embedded credentials, query or fragment; a local S3 endpoint may use HTTP"
            )
        return value.rstrip("/")


class Media(StrictModel):
    engine: Literal["builtin", "reel-studio"] = "builtin"
    transcription_model: str = ""
    transcription_python: str = ""
    transcription_language: str = Field("", max_length=12)
    asset_catalog: str = ""
    asset_root: str = ""
    editorial_preferences: str = ""


class Perception(StrictModel):
    provider: Literal["none", "ollama", "gemini", "command"] = "none"
    model: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    command: list[str] = Field(default_factory=list)
    allow_cloud_upload: bool = False
    timeout_seconds: int = Field(240, ge=15, le=1200)
    max_seconds: int = Field(120, ge=5, le=180)
    max_megabytes: int = Field(20, ge=1, le=40)


class Web(StrictModel):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1024, le=65535)
    public_base_url: str = ""

    @field_validator("public_base_url")
    @classmethod
    def origin_only(cls, value):
        if not value:
            return value
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Public web URL must be an HTTPS origin without a path, credentials, query or fragment"
            )
        return value.rstrip("/")


class Auth(StrictModel):
    twitch_client_id: str = ""
    instagram_app_id: str = ""
    instagram_redirect_uri: str = ""


class Channel(StrictModel):
    name: str
    enabled: bool = True
    chat: Literal["eventsub", "anonymous-irc"] = "anonymous-irc"
    attribution: str = Field("", max_length=400)

    _name = field_validator("name")(channel_name)


class Settings(StrictModel):
    data_dir: str = "runtime"
    reel_studio: str = ".."
    database_url: str = ""
    capture: Capture = Field(default_factory=Capture)
    detector: Detector = Field(default_factory=Detector)
    agent: Agent = Field(default_factory=Agent)
    editing: Editing = Field(default_factory=Editing)
    media: Media = Field(default_factory=Media)
    perception: Perception = Field(default_factory=Perception)
    publish: Publish = Field(default_factory=Publish)
    web: Web = Field(default_factory=Web)
    auth: Auth = Field(default_factory=Auth)
    channels: list[Channel] = Field(default_factory=list)

    @field_validator("database_url")
    @classmethod
    def managed_database_location(cls, value):
        if value:
            from .db import url_for

            try:
                url_for(".", value)
            except RelayError as exc:
                raise ValueError(str(exc)) from None
        return value

    @model_validator(mode="after")
    def unique_channels(self):
        names = [c.name for c in self.channels]
        if len(names) != len(set(names)):
            raise ValueError("A Twitch channel can only appear once")
        return self


DEFAULTS = Settings().model_dump()


def load(path: str | Path = "config.toml") -> dict:
    path = Path(path).resolve()
    incoming = tomllib.loads(path.read_text()) if path.exists() else {}
    cfg = Settings.model_validate(incoming).model_dump()
    cfg["config_path"] = str(path)
    for key in ("data_dir", "reel_studio"):
        cfg[key] = str((path.parent / cfg[key]).resolve())
    cfg["database_url"] = os.environ.get("REEL_RELAY_DATABASE_URL", cfg["database_url"])
    # Environment overrides must obey the same boundary as saved settings. The
    # local SQLite filename is fixed so media sandbox policies can protect it.
    from .db import url_for

    url_for(cfg["data_dir"], cfg["database_url"])
    return cfg


@contextmanager
def _config_lock(path):
    """Serialize writers on supported macOS/Linux hosts using a stable sidecar.

    The lock must not be the TOML file itself because saving replaces its inode.
    Shared mounts must support POSIX advisory locks; external text editors must
    use the versioned save API to participate in lost-update protection.
    """
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name("." + path.name + ".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _version(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def _check_version(path, expected_version):
    if expected_version is not None and _version(path) != expected_version:
        raise ConfigurationConflict("Configuration changed; reload the latest settings before saving")


def load_versioned(path: str | Path = "config.toml") -> tuple[dict, str]:
    """Return a consistent settings snapshot and its optimistic-write token."""
    path = Path(path).resolve()
    with _config_lock(path):
        return load(path), _version(path)


def _save_locked(path, values):
    clean = {k: v for k, v in values.items() if k != "config_path"}
    original = tomllib.loads(path.read_text()) if path.exists() else {}
    # load() resolves paths for consumers. An unrelated UI edit must not turn a
    # portable relative path into this machine's absolute filesystem location.
    for key in ("data_dir", "reel_studio"):
        before = original.get(key, DEFAULTS[key])
        if (
            before is not None
            and not Path(before).is_absolute()
            and clean.get(key) == str((path.parent / before).resolve())
        ):
            clean[key] = before
    # Runtime overrides can contain a database password. Editing unrelated
    # preferences must never copy an environment credential into the TOML file.
    if "REEL_RELAY_DATABASE_URL" in os.environ:
        clean["database_url"] = original.get("database_url", "")
    settings = Settings.model_validate(clean)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            tomli_w.dump(settings.model_dump(), file)
            file.flush()
            os.fsync(file.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save(path, values, *, expected_version=None):
    """Replace all settings; use mutate for field-specific concurrent updates."""
    path = Path(path).resolve()
    with _config_lock(path):
        _check_version(path, expected_version)
        _save_locked(path, values)


def mutate(path, change, *, expected_version=None):
    """Reload under a writer lock, apply change(cfg), validate, and return settings.

    The callback mutates only the fields it owns and performs no external work.
    Unrelated concurrent changes (including a publishing pause) are preserved.
    Pass a token from load_versioned when the caller must reject stale input.
    """
    path = Path(path).resolve()
    with _config_lock(path):
        _check_version(path, expected_version)
        current = load(path)
        change(current)
        _save_locked(path, current)
        return load(path)
