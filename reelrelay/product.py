"""Read-only product projections; no model, capture, or publication side effects."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import mimetypes
import os
import shutil
import sys
from pathlib import Path

import sqlalchemy as sa

from . import db, media
from .credentials import Vault
from .studio import Studio
from .util import RelayError, inside


def readiness(cfg):
    checks = []
    sections = {
        "sources": "sources",
        "editor": "editor",
        "perception": "editor",
        "instagram": "instagram",
        "delivery": "delivery",
    }

    def add(key, label, ready, detail, *, optional=False):
        checks.append(
            {
                "id": key,
                "label": label,
                "state": "ready" if ready else "warning" if optional else "missing",
                "detail": detail,
                **(
                    {"action": {"label": "Open setup", "href": "/setup#" + sections.get(key, "editor")}}
                    if not ready
                    else {}
                ),
            }
        )

    enabled = [c for c in cfg["channels"] if c["enabled"]]
    live = importlib.util.find_spec("streamlink") is not None
    add("sources", "Stream sources", bool(enabled), f"{len(enabled)} enabled sources")
    add("capture", "Live capture", live, "Installed" if live else "Install the live extra")
    try:
        media.binary("ffmpeg")
        media.binary("ffprobe")
        ffmpeg = True
    except RelayError:
        ffmpeg = False
    add("ffmpeg", "Video tools", ffmpeg, "FFmpeg and FFprobe found" if ffmpeg else "Install FFmpeg")
    engine = cfg["media"]["engine"]
    if engine == "builtin":
        state = Studio(cfg).readiness()
        transcription = state["transcription_runtime"] and state["transcription_model"]
        boundary = state["offline_boundary"]
    else:
        transcription = (Path(cfg["data_dir"]) / "studio/snapshot.json").is_file()
        boundary = sys.platform == "darwin"
    add(
        "transcription",
        "Local transcription",
        transcription,
        "Ready on this machine" if transcription else "Install a local transcription model in Setup",
    )
    add(
        "isolation",
        "Offline media processing",
        boundary,
        "Offline sandbox available" if boundary else "Install Linux bubblewrap",
    )
    provider = cfg["agent"]["provider"]
    editorial = (
        provider == "codex"
        and shutil.which("codex") is not None
        or provider == "command"
        and bool(cfg["agent"]["command"])
        or provider == "gemini"
        and cfg["agent"]["allow_cloud_upload"]
        and bool(cfg["agent"]["model"])
        and bool(os.environ.get("GEMINI_API_KEY") or Vault(cfg).exists("gemini"))
        or provider == "ollama"
        and bool(cfg["agent"]["model"])
    )
    add(
        "editor",
        "Automatic editor",
        editorial,
        f"{provider}; access is verified when processing" if editorial else "Choose an editorial provider",
    )
    perception = cfg["perception"]
    speech_check = cfg["agent"].get("selection_mode") == "throughput" and engine == "builtin"
    perceptual = perception["provider"] != "none"
    if perception["provider"] == "gemini":
        perceptual = bool(
            perception["allow_cloud_upload"]
            and perception["model"]
            and (os.environ.get("GEMINI_API_KEY") or Vault(cfg).exists("gemini"))
        )
    elif perception["provider"] == "ollama":
        perceptual = bool(perception["model"])
    elif perception["provider"] == "command":
        perceptual = bool(perception["command"])
    if speech_check:
        perceptual = bool(transcription and boundary and ffmpeg)
    add(
        "perception",
        "Rendered audio check" if speech_check else "Sound and motion review",
        perceptual,
        "Local speech recovery, caption agreement and audio measurements"
        if speech_check and perceptual
        else "Configured; each review records the input it received"
        if perceptual
        else "Image-only review cannot clear the audio check",
        optional=True,
    )
    instagram = Vault(cfg).account_status("instagram")
    connected = instagram["connected"]
    add(
        "instagram",
        "Instagram account",
        connected,
        f"Connected as @{instagram.get('username', '')}"
        if connected
        else "Connect a professional Instagram account",
    )
    delivery = bool(
        cfg["web"]["public_base_url"] or (cfg["publish"]["public_media_base"] and cfg["publish"]["s3_bucket"])
    )
    add(
        "delivery",
        "Video delivery",
        delivery,
        "HTTPS delivery configured" if delivery else "Instagram needs a reachable HTTPS video URL",
    )
    can_edit = bool(ffmpeg and boundary and transcription and editorial)
    return {
        "checks": checks,
        "can_capture": bool(enabled and live and ffmpeg and boundary),
        "can_edit": can_edit,
        "can_publish": bool(connected and delivery),
        "auto_publish_enabled": bool(
            cfg["publish"]["enabled"] and not cfg["publish"]["dry_run"] and cfg["publish"]["auto_approve"]
        ),
        "media_engine": engine,
        "perception": {
            "provider": "local-asr" if speech_check else perception["provider"],
            "modalities": (
                ["audio", "captions"]
                if speech_check
                else ["audio"]
                if perception["provider"] == "ollama"
                else ["audio", "video"]
            )
            if perceptual
            else [],
            "configured": bool(perceptual),
        },
    }


def asset_records(cfg):
    studio = Studio(cfg)
    if cfg["media"]["engine"] == "builtin":
        rows = studio._catalog_rows()
        root = Path(cfg["data_dir"])
    else:
        if not (studio.root / "snapshot.json").is_file():
            return []
        rows, root = studio.catalog(), studio.root
    records = []
    for row in rows:
        path = inside(root / row["path"], cfg["data_dir"])
        if not path.is_file() or any(
            path.is_relative_to(Path(cfg["data_dir"]) / x) for x in (".private", "secrets")
        ):
            continue
        key = hashlib.sha256(str(path.relative_to(cfg["data_dir"])).encode()).hexdigest()[:24]
        records.append(
            {
                "id": key,
                "title": row.get("title") or path.stem,
                "kind": row["kind"],
                "media_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "tags": row.get("tags") or [],
                "duration": row.get("duration"),
                "rights": row.get("rights") or "No usage rights recorded",
                "attribution": row.get("attribution") or "",
                "preview_url": f"/api/assets/{key}/preview",
                "_path": path,
            }
        )
    return records


EVENT_LABELS = {
    "editorial_decision": "Editorial selection completed",
    "preview_review": "Rendered clip reviewed",
    "stage_completed": "Processing stage completed",
    "job_failed": "A clip needs attention",
    "job_retry_scheduled": "Processing retry scheduled",
    "publishing_settings_changed": "Publishing settings changed",
    "configuration_updated": "Workspace settings saved",
    "approved": "Clip approved",
    "edit_changes_requested": "Edit changes requested",
    "operator_preview_review": "Clip reviewed by the operator",
    "manual_publication_recorded": "Manual publication recorded",
    "published": "Reel published",
    "worker_lease_lost": "Worker ownership expired; stale result discarded",
    "publication_attempt": "Publication submitted",
    "candidate_created": "New moment captured",
    "perception_completed": "Media observations recorded",
    "reassessment_requested": "Source queued for another editorial assessment",
    "detail_inspection_requested": "Editor requested a closer look at the action",
}


def activity(store, limit=50, before=None):
    query = sa.select(db.events).order_by(db.events.c.id.desc()).limit(limit + 1)
    if before is not None:
        query = query.where(db.events.c.id < before)
    with store.connect() as connection:
        rows = connection.execute(query).mappings().all()
    items = []
    for row in rows[:limit]:
        detail = json.loads(row["detail"])
        kind = row["kind"]
        summary = EVENT_LABELS.get(kind, kind.replace("_", " ").capitalize())
        if kind == "stage_completed" and detail.get("stage") in {
            "preparing",
            "judging",
            "rendering",
            "reviewing",
            "revising",
        }:
            summary = detail["stage"].capitalize() + " completed"
        if kind == "publishing_settings_changed":
            summary = "Publishing enabled" if detail.get("enabled") else "Publishing paused"
        items.append(
            {
                "id": row["id"],
                "at": row["at"],
                "kind": kind,
                "candidate_id": row["candidate_id"],
                "summary": summary,
                "level": "error"
                if "failed" in kind
                else "warning"
                if any(term in kind for term in ("retry", "unknown", "lost"))
                else "info",
            }
        )
    return {"items": items, "next_before": items[-1]["id"] if len(rows) > limit else None}
