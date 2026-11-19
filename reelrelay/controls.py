"""Persistent UI controls and fixed, supervised local service entry points."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import sqlalchemy as sa

from . import db
from .store import Store
from .util import RelayError

SERVICES = {"capture", "editor"}
MODELS = {"tiny", "base", "small", "medium", "large-v3-turbo"}
HEARTBEAT_SECONDS = 20


def service_enabled(cfg, service):
    if service not in SERVICES:
        raise RelayError("Unknown workflow service")
    return Store(cfg).get_setting("workflow:" + service, True) is True


def service_health(store, name):
    with store.connect() as connection:
        row = (
            connection.execute(sa.select(db.health).where(db.health.c.channel == "@service:" + name))
            .mappings()
            .first()
        )
    if not row:
        return None
    return {"heartbeat_at": row["updated_at"], **json.loads(row["detail"])}


def installation_status(cfg):
    store = Store(cfg)
    value = store.get_setting("media_install", {})
    state = value.get("status", "idle")
    if state == "running":
        health = service_health(store, "install") or {}
        if health.get("job_id") != value.get("id"):
            health = {}
        last = max(health.get("heartbeat_at", 0), value.get("started_at", 0))
        if time.time() - last > 120:
            state = "failed"
    return {
        "status": state,
        "model": value.get("model"),
        "message": (
            "Installation was interrupted. You can safely try again."
            if state == "failed" and value.get("status") == "running"
            else value.get("message", "Choose a transcription model to install locally.")
        ),
    }


def system_status(cfg):
    store, now = Store(cfg), time.time()
    supervisor = service_health(store, "supervisor") or {}
    managed = now - supervisor.get("heartbeat_at", 0) <= HEARTBEAT_SECONDS and supervisor.get("state") in {
        "running",
        "starting",
    }
    services = {}
    for name in sorted(SERVICES):
        health = service_health(store, name) or {}
        fresh = managed and now - health.get("heartbeat_at", 0) <= HEARTBEAT_SECONDS
        enabled = service_enabled(cfg, name)
        state = health.get("state", "stopped") if fresh else "stopped"
        if state not in {"running", "paused", "pausing", "stopped", "starting"}:
            state = "stopped"
        services[name] = {"enabled": enabled, "state": state, "heartbeat_at": health.get("heartbeat_at")}
    return {"managed": managed, "services": services, "installation": installation_status(cfg)}


def _launch(cfg, arguments, log_name):
    config = cfg.get("config_path")
    if not config:
        raise RelayError("This server needs a saved workspace configuration")
    from .config import mutate

    if not Path(config).is_file():
        mutate(config, lambda value: None)
    folder = Path(cfg["data_dir"]) / ".private" / "services"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    log = folder / log_name
    # Keep one previous bounded diagnostic file; never expose command output in an API.
    if log.is_file() and log.stat().st_size > 4 * 1024**2:
        log.replace(log.with_suffix(".previous.log"))
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "HOME",
            "CODEX_HOME",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "SSL_CERT_FILE",
            "REEL_RELAY_DATABASE_URL",
            "REEL_RELAY_ENCRYPTION_KEY",
            "REEL_RELAY_WEB_TOKEN",
            "GEMINI_API_KEY",
            "REEL_RELAY_S3_ACCESS_KEY_ID",
            "REEL_RELAY_S3_SECRET_ACCESS_KEY",
        }
    }
    if arguments[0] == "install-job":
        for key in (
            "REEL_RELAY_ENCRYPTION_KEY",
            "REEL_RELAY_WEB_TOKEN",
            "GEMINI_API_KEY",
            "REEL_RELAY_S3_ACCESS_KEY_ID",
            "REEL_RELAY_S3_SECRET_ACCESS_KEY",
            "CODEX_HOME",
        ):
            environment.pop(key, None)
    with log.open("ab", buffering=0) as output:
        child = subprocess.Popen(
            [sys.executable, "-u", "-B", "-m", "reelrelay", "--config", config, *arguments],
            cwd=Path(config).parent,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=environment,
        )
    log.chmod(0o600)
    return child.pid


def control_service(cfg, service, enabled):
    if service not in SERVICES or not isinstance(enabled, bool):
        raise RelayError("Choose capture or editor and an enabled state")
    store = Store(cfg)
    with store.lock("workflow-control", seconds=30):
        store.set_setting("workflow:" + service, enabled)
        if enabled and not system_status(cfg)["managed"]:
            pid = _launch(cfg, ["services"], "workflow.log")
            store.health("@service:supervisor", state="starting", pid=pid)
            for name in SERVICES:
                store.health("@service:" + name, state="starting")
        store.event("workflow_control_changed", {"service": service, "enabled": enabled})
    return system_status(cfg)


def request_install(cfg, model):
    if model not in MODELS:
        raise RelayError("Choose a supported transcription model")
    store = Store(cfg)
    with store.lock("media-install-request", seconds=30):
        current = installation_status(cfg)
        if current["status"] == "running":
            if current["model"] != model:
                raise RelayError("Wait for the current model installation to finish")
            return current
        job_id = uuid.uuid4().hex
        store.set_setting(
            "media_install",
            {
                "id": job_id,
                "status": "running",
                "model": model,
                "started_at": time.time(),
                "message": "Installing locked transcription dependencies and downloading the selected model…",
            },
        )
        try:
            _launch(cfg, ["install-job", "--model", model, "--job-id", job_id], "installation.log")
        except (OSError, ValueError, RelayError):
            store.set_setting(
                "media_install",
                {
                    "id": job_id,
                    "status": "failed",
                    "model": model,
                    "message": "Could not start installation. Check the server's Python installation.",
                },
            )
            raise RelayError("Could not start the local model installer") from None
    return installation_status(cfg)


def run_install_job(cfg, model, job_id):
    from .install import setup_media

    store, stopped = Store(cfg), threading.Event()
    with store.lock("media-install", seconds=90) as guard:
        current = store.get_setting("media_install", {})
        if current.get("id") != job_id or current.get("status") != "running":
            raise RelayError("This installation request is no longer active")

        def heartbeat():
            while not stopped.is_set():
                if guard.lost.is_set() or store.get_setting("media_install", {}).get("id") != job_id:
                    return
                store.health("@service:install", state="running", job_id=job_id)
                stopped.wait(5)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            setup_media(cfg, model, download=True)
            result = {
                "status": "succeeded",
                "message": "Transcription is installed and selected. No footage was uploaded.",
            }
        except Exception:  # noqa: BLE001 -- persist a sanitized outcome at the process boundary
            result = {
                "status": "failed",
                "message": "Installation failed. Check disk space and network access, then retry. Details are in the local installation log.",
            }
        finally:
            stopped.set()
            thread.join(timeout=6)
        with store.lock("media-install-request", seconds=30):
            if guard.lost.is_set() or store.get_setting("media_install", {}).get("id") != job_id:
                return
            store.set_setting("media_install", {"id": job_id, "model": model, **result})
            store.health("@service:install", state=result["status"], job_id=job_id)
