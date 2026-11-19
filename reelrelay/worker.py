from __future__ import annotations

import random
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from . import media
from .agent import judge, validate_decision, validate_review
from .config import Editing, load
from .content_flags import held_flags
from .controls import service_enabled
from .perception import inspect_media, read_verified
from .store import LeaseLost, Store
from .studio import Studio
from .util import APIError, RelayError, TransientError, inside, read_json, sha256, write_json

STAGES = [
    ("needs_preview_review", "reviewing"),
    ("preview_changes", "revising"),
    ("selected", "rendering"),
    ("needs_agent", "judging"),
    ("captured", "preparing"),
]
MODEL_STAGES = {"judging", "reviewing", "revising"}
REVISION_STATES = {
    "ready",
    "approved",
    "needs_preview_review",
    "preview_changes",
    "needs_human_review",
    "rejected",
}


def request_changes(cfg, cid, notes, version):
    """Requeue a reviewed export without losing the previous version or its notes."""
    notes = notes.strip()
    if not 3 <= len(notes) <= 1500:
        raise RelayError("Describe the requested changes in 3–1500 characters")
    store = Store(cfg)
    candidate = store.get(cid)
    if candidate["status"] not in REVISION_STATES or not candidate["output_path"]:
        raise RelayError("Wait for a finished edit before requesting changes")
    if candidate["updated_at"] != version:
        raise LeaseLost("This clip changed; reload its latest version before giving feedback")
    if sha256(candidate["output_path"]) != candidate["output_hash"]:
        raise RelayError("Export changed on disk; review the correct version first")
    metadata = candidate["metadata"]
    feedback = metadata.get("edit_feedback", [])[-19:]
    feedback.append({"at": time.time(), "notes": notes, "output_hash": candidate["output_hash"]})
    metadata.update(edit_feedback=feedback, revisions=0)
    store.update(
        cid,
        expected=candidate["status"],
        expected_version=version,
        status="preview_changes",
        metadata=metadata,
        review={
            "approved": False,
            "confidence": 1,
            "issues": [notes],
            "notes": "Changes requested by the operator: " + notes,
            "audio_review": "not_listened",
        },
        available_at=0,
        error=None,
    )
    store.event("edit_changes_requested", {"notes": notes, "output_hash": candidate["output_hash"]}, cid)
    return store.get(cid)


def record_manual_post(cfg, cid, permalink, version):
    """Record the operator's completed upload; this never calls Instagram."""
    parsed = urlparse(permalink.strip())
    if (
        parsed.scheme != "https"
        or parsed.netloc not in ("www.instagram.com", "instagram.com")
        or not re.fullmatch(r"/(?:reel|p)/[A-Za-z0-9_-]{5,80}/?", parsed.path)
    ):
        raise RelayError("Paste the Instagram link to the Reel you already posted")
    store = Store(cfg)
    candidate = store.get(cid)
    if (
        candidate["is_test"]
        or candidate["status"] not in {"ready", "approved"}
        or not (candidate["review"] or {}).get("approved")
    ):
        raise RelayError("Only a reviewed, unpublished real clip can be recorded as manually posted")
    if sha256(candidate["output_path"]) != candidate["output_hash"]:
        raise RelayError("Export changed since review; verify the actual file you posted")
    permalink = "https://www.instagram.com" + parsed.path.rstrip("/") + "/"
    metadata = {**candidate["metadata"], "publication_method": "manual", "publication_verified_by_api": False}
    store.update(
        cid,
        expected=candidate["status"],
        expected_version=version,
        status="published",
        published_at=time.time(),
        permalink=permalink,
        metadata=metadata,
    )
    store.event("manual_publication_recorded", {"permalink": permalink, "api_verified": False}, cid)
    return store.get(cid)


def record_operator_review(cfg, cid, notes, version):
    """Record a human's viewing/listening check without enabling publication."""
    notes = notes.strip()
    if not 3 <= len(notes) <= 1500:
        raise RelayError("Add a short note about the finished edit you checked")
    store = Store(cfg)
    candidate = store.get(cid)
    if (
        candidate["status"] not in {"needs_human_review", "needs_preview_review"}
        or not candidate["output_path"]
    ):
        raise RelayError("This clip is not waiting for a human preview check")
    if candidate["updated_at"] != version:
        raise LeaseLost("This clip changed; reload and review the latest export")
    if sha256(candidate["output_path"]) != candidate["output_hash"]:
        raise RelayError("Export changed since rendering; review the correct file")
    qa_path = Path(candidate["output_path"]).parent / "qa.json"
    qa = read_json(qa_path) if qa_path.is_file() else {}
    if qa.get("full_decode") != "passed" or qa.get("warnings") != []:
        raise RelayError("The export must pass technical checks before a human can clear it")
    metadata = candidate["metadata"]
    history = metadata.get("operator_reviews", [])[-19:]
    history.append(
        {
            "at": time.time(),
            "notes": notes,
            "output_hash": candidate["output_hash"],
            "previous_review": candidate["review"],
        }
    )
    metadata["operator_reviews"] = history
    store.update(
        cid,
        expected=candidate["status"],
        expected_version=version,
        status="ready",
        metadata=metadata,
        review={
            "approved": True,
            "confidence": 1,
            "issues": [],
            "notes": "Operator review: " + notes,
            "audio_review": "operator_confirmed",
        },
        error=None,
    )
    store.event(
        "operator_preview_review",
        {"notes": notes, "output_hash": candidate["output_hash"], "audio_review": "operator_confirmed"},
        cid,
    )
    return store.get(cid)


def _caption_with_credits(caption, credits):
    caption = caption.strip()
    for credit in dict.fromkeys(str(c).strip() for c in credits if c):
        if credit and credit not in caption:
            caption += ("\n\n" if caption else "") + credit
    return caption


def apply_decision(cfg, cid, value, *, claim_token=None, metadata_updates=None):
    store, candidate = Store(cfg), Store(cfg).get(cid)
    allowed = {
        "needs_agent",
        "judging",
        "editorial_review",
        "revising",
        "preview_changes",
        "needs_human_review",
    }
    if candidate["status"] not in allowed:
        raise RelayError("This candidate is not waiting for an editorial decision")
    if candidate["status"] in ("judging", "revising") and claim_token != candidate.get("claim_token"):
        raise LeaseLost("Another worker owns this editorial decision")
    evidence = read_json(candidate["evidence_path"])
    editing = Editing.model_validate(cfg.get("editing", {})).model_dump()
    value = validate_decision(value, evidence, editing=editing)
    settings = cfg["agent"]
    if value["verdict"] == "reject":
        status = "rejected"
    elif (
        value["verdict"] != "accept"
        or held_flags(value["flags"], settings.get("allowed_flags", []))
        or (settings.get("selection_mode") != "throughput" and value["score"] < settings["min_score"])
        or value["confidence"] < settings["min_confidence"]
    ):
        status = "editorial_review"
    else:
        status = "selected"
    requested = {effect["asset_path"] for kind in ("sfx", "overlays") for effect in value[kind]}
    credits = [candidate["attribution"]] + [
        row.get("attribution", "") for row in evidence["assets"] if row["path"] in requested
    ]
    caption = _caption_with_credits(value["caption"], credits)
    error = None
    if status == "selected" and len(caption) > 2200:
        status = "editorial_review"
        error = "Caption and required credits exceed 2200 characters; reassess with a shorter caption or fewer assets."
    store.update(
        cid,
        expected=candidate["status"],
        claim_token=claim_token,
        status=status,
        decision=value,
        score=value["score"],
        confidence=value["confidence"],
        caption=caption,
        metadata={**candidate["metadata"], **(metadata_updates or {}), "editing_settings": editing},
        error=error,
    )
    store.event(
        "editorial_decision", {"verdict": value["verdict"], "status": status, "score": value["score"]}, cid
    )
    return store.get(cid)


def apply_review(cfg, cid, value, *, claim_token=None):
    store, candidate = Store(cfg), Store(cfg).get(cid)
    if candidate["status"] not in ("needs_preview_review", "reviewing", "preview_changes"):
        raise RelayError("This candidate is not waiting for a rendered-preview review")
    if candidate["status"] == "reviewing" and claim_token != candidate.get("claim_token"):
        raise LeaseLost("Another worker owns this preview review")
    value = validate_review(value)
    audio_path = candidate["metadata"].get("preview_perception")
    audio_checked = False
    if audio_path:
        path = inside(Path(cfg["data_dir"]) / audio_path, cfg["data_dir"])
        if sha256(path) != candidate["metadata"].get("preview_perception_hash"):
            raise RelayError("Audio review evidence changed; repeat preview review")
        receipt = read_verified(path, candidate["output_hash"], cfg["data_dir"])
        assessment = receipt["assessment"]
        technical_only = receipt["provider"] == "local-technical"
        audio_checked = (
            cfg["agent"].get("selection_mode") == "throughput"
            and cfg["media"]["engine"] == "builtin"
            and receipt.get("check_type") == "technical-audio"
            and receipt.get("passed") is True
            and receipt.get("speech_assessed") is False
            if technical_only
            else assessment["confidence"] >= cfg["agent"]["min_confidence"]
        )
        defects = assessment["audio_issues"] + assessment["visual_issues"]
        if defects:
            value = {**value, "approved": False, "issues": list(dict.fromkeys(value["issues"] + defects))}
        value["audio_review"] = (
            "technical_assessed"
            if technical_only
            else "asr_assessed"
            if receipt["provider"] == "local-asr"
            else "model_assessed"
        )
        value["perception_provider"] = receipt["provider"]
        value["perception_model"] = receipt["model"]
    passed = value["approved"] and value["confidence"] >= cfg["agent"]["min_confidence"]
    status = (
        "needs_human_review"
        if passed and not audio_checked
        else "approved"
        if passed and cfg["publish"]["auto_approve"]
        else "ready"
        if passed
        else "preview_changes"
    )
    store.update(
        cid, expected=candidate["status"], claim_token=claim_token, status=status, review=value, error=None
    )
    store.event(
        "preview_review", {"approved": passed and audio_checked, "audio_review": value["audio_review"]}, cid
    )
    return store.get(cid)


def approve(cfg, cid, version=None):
    store = Store(cfg)
    candidate = store.get(cid)
    if candidate["status"] != "ready" or not (candidate["review"] or {}).get("approved"):
        raise RelayError("Only an exported clip with a passed preview review can be approved")
    if sha256(candidate["output_path"]) != candidate["output_hash"]:
        raise RelayError("Export changed since its preview review")
    store.update(
        cid, expected="ready", expected_version=version or candidate["updated_at"], status="approved"
    )
    store.event("approved", {}, cid)


def reassess(cfg, cid, version, notes=""):
    """Queue a fresh editorial judgment without treating a retry as acceptance."""
    if len(notes) > 1500:
        raise RelayError("Keep reassessment context under 1500 characters")
    store = Store(cfg)
    item = store.get(cid)
    if item["status"] not in {"editorial_review", "rejected"} or not item["evidence_path"]:
        raise RelayError("Only a held or passed-over source can be reassessed")
    metadata = dict(item["metadata"])
    history = metadata.get("selection_history", [])[-9:]
    history.append({"at": time.time(), "decision": item["decision"], "notes": notes.strip()})
    metadata.update(selection_history=history, reassessment_notes=notes.strip())
    store.update(
        cid,
        expected=item["status"],
        expected_version=version,
        status="needs_agent",
        metadata=metadata,
        error=None,
    )
    store.event("reassessment_requested", {}, cid)


def _run_stage(cfg, store, studio, candidate):
    cid, working, token = candidate["id"], candidate["status"], candidate["claim_token"]
    if working == "preparing":
        project, evidence = studio.prepare(candidate)
        store.update(
            cid,
            expected=working,
            claim_token=token,
            status="needs_agent",
            project_path=str(project),
            evidence_path=str(evidence),
            error=None,
        )
    elif working == "judging":
        packet = _with_perception(cfg, store, candidate, candidate["evidence_path"], preview=False)
        if candidate["metadata"].get("reassessment_notes"):
            original = read_json(packet)
            original["operator_feedback"] = [{"notes": candidate["metadata"]["reassessment_notes"]}]
            packet = Path(packet).parent / ("reassess-" + token + ".json")
            write_json(packet, original)
        value = judge(cfg, packet)
        windows = value.get("inspection_windows", [])
        if value["verdict"] == "needs_review" and windows and not candidate["metadata"].get("detail_passes"):
            # Uncertainty can request more evidence once; it never grants acceptance.
            if sha256(candidate["source_path"]) != candidate["source_hash"]:
                raise RelayError("Source changed before detailed inspection")
            folder = Path(cfg["data_dir"]) / "evidence" / cid / ("detail-" + token)
            images = media.detail_contact_sheets(candidate["source_path"], folder, cfg["data_dir"], windows)
            original = read_json(packet)
            original["images"].extend(str(path) for path in images)
            original["detail_inspection"] = {"windows": windows, "target_frames_per_second": 4, "pass": 1}
            original["previous_decision"] = value
            enriched = folder / "evidence.json"
            write_json(enriched, original)
            metadata = {**candidate["metadata"], "detail_passes": 1}
            store.update(
                cid,
                expected=working,
                claim_token=token,
                status="needs_agent",
                evidence_path=str(enriched),
                metadata=metadata,
                decision=value,
                score=value["score"],
                confidence=value["confidence"],
                error=None,
            )
            store.event("detail_inspection_requested", {"windows": windows, "frames": len(images)}, cid)
        else:
            apply_decision(cfg, cid, value, claim_token=token)
    elif working == "rendering":
        editing = candidate["metadata"].get("editing_settings", cfg.get("editing", {}))
        preferences = Editing.model_validate(editing).model_dump()
        if cfg.get("media", {}).get("engine") == "reel-studio" and (
            not preferences["captions_enabled"] or preferences["caption_words"] != 3
        ):
            raise RelayError("Configurable speech captions require the built-in media engine")
        project, export, review_evidence = studio.render(candidate)
        credits = [a.get("attribution", "").strip() for a in read_json(export / "assets.json")]
        caption = _caption_with_credits(candidate["caption"], credits)
        caption_issue = (
            "Caption and required credits exceed 2200 characters; shorten the caption or use fewer assets."
            if len(caption) > 2200
            else None
        )
        store.update(
            cid,
            expected=working,
            claim_token=token,
            status="preview_changes" if caption_issue else "needs_preview_review",
            project_path=str(project),
            output_path=str(export / "reel.mp4"),
            output_hash=sha256(export / "reel.mp4"),
            metadata={
                **{
                    k: v
                    for k, v in candidate["metadata"].items()
                    if k not in {"preview_perception", "preview_perception_hash"}
                },
                "review_evidence": store.file_key(review_evidence),
                "review_evidence_hash": sha256(review_evidence),
            },
            caption=caption,
            review={
                "approved": False,
                "confidence": 1,
                "issues": [caption_issue],
                "notes": caption_issue,
                "audio_review": "not_listened",
            }
            if caption_issue
            else None,
            error=None,
        )
    elif working == "revising":
        original = read_json(candidate["evidence_path"])
        count = candidate["metadata"].get("revisions", 0) + 1
        original.update(
            previous_decision=candidate["decision"],
            preview_feedback=candidate["review"],
            operator_feedback=candidate["metadata"].get("edit_feedback", [])[-3:],
            revision_instruction="Revise the edit to fix the preview issues. Preserve a complete story.",
        )
        preview = Path(candidate["output_path"]).parent / "contact-sheet.jpg"
        if preview.is_file():
            original["images"].append(str(preview))
        packet = Path(candidate["evidence_path"]).parent / f"revision-{count}-{token}.json"
        write_json(packet, original)
        apply_decision(cfg, cid, judge(cfg, packet), claim_token=token, metadata_updates={"revisions": count})
    else:
        committed = candidate["metadata"].get("review_evidence")
        packet = (
            inside(Path(cfg["data_dir"]) / committed, cfg["data_dir"])
            if committed
            else Path(candidate["evidence_path"]).parent / "review-evidence.json"
        )
        if committed and sha256(packet) != candidate["metadata"].get("review_evidence_hash"):
            raise RelayError("Preview evidence changed since rendering; render a new attempt")
        packet = _with_perception(cfg, store, candidate, packet, preview=True)
        apply_review(cfg, cid, judge(cfg, packet), claim_token=token)


def _with_perception(cfg, store, candidate, evidence_path, *, preview):
    """Bind expensive observations to exact bytes; reuse only validated receipts."""
    throughput = cfg["agent"].get("selection_mode") == "throughput"
    # Routine volume uses timed source ASR and checks the final rendered speech.
    # Generative perception remains an optional, more expensive inspection mode.
    if throughput and not preview:
        return evidence_path
    speech_check = throughput and preview and cfg["media"]["engine"] == "builtin"
    provider = "local-asr" if speech_check else cfg["perception"]["provider"]
    model = Studio(cfg).model_path().name if speech_check else cfg["perception"]["model"]
    if provider == "none":
        return evidence_path
    source = candidate["output_path"] if preview else candidate["source_path"]
    digest = candidate["output_hash"] if preview else candidate["source_hash"]
    key = "preview_perception" if preview else "source_perception"
    metadata = dict(candidate["metadata"])
    report = None
    if metadata.get(key):
        previous = inside(Path(cfg["data_dir"]) / metadata[key], cfg["data_dir"])
        if previous.is_file() and sha256(previous) == metadata.get(key + "_hash"):
            packet = read_json(previous)
            if packet.get("source_sha256") == digest and (
                packet.get("provider") == provider
                and packet.get("model") == model
                or speech_check
                and packet.get("provider") == "local-technical"
                and packet.get("model") == "ffmpeg"
                and packet.get("check_type") == "technical-audio"
            ):
                report = previous
    if report is None:
        if speech_check:
            from .audio_review import inspect_rendered_speech

            report = inspect_rendered_speech(cfg, candidate)
        else:
            report = inspect_media(cfg, source, purpose="preview" if preview else "selection")
    observed = read_verified(report, digest, cfg["data_dir"])
    original = read_json(evidence_path)
    original["perception"] = observed
    enriched = report.parent / "editorial-evidence.json"
    # Each model stage gets its own evidence file, even when observations are reused.
    if enriched.exists():
        enriched = report.parent / ("editorial-evidence-" + candidate["claim_token"] + ".json")
    write_json(enriched, original)
    metadata.update({key: store.file_key(report), key + "_hash": sha256(report)})
    changes = {"metadata": metadata}
    if preview:
        metadata.update(review_evidence=store.file_key(enriched), review_evidence_hash=sha256(enriched))
    else:
        changes["evidence_path"] = str(enriched)
    store.update(
        candidate["id"], expected=candidate["status"], claim_token=candidate["claim_token"], **changes
    )
    candidate["metadata"] = metadata
    store.event(
        "perception_completed",
        {
            "provider": observed["provider"],
            "modalities": observed["modalities"],
            "purpose": observed["purpose"],
        },
        candidate["id"],
    )
    return enriched


def _retryable(error):
    return (
        isinstance(error, (TransientError, TimeoutError, subprocess.TimeoutExpired))
        or isinstance(error, APIError)
        and error.status in (408, 429, 500, 502, 503, 504)
    )


def step(cfg, *, cid=None, stage=None):
    store, studio = Store(cfg), Studio(cfg)
    store.recover()
    for ready, working in STAGES:
        if stage and stage != working:
            continue
        if working in MODEL_STAGES and (
            cfg["agent"]["provider"] == "manual"
            or store.recent_agent_calls() >= cfg["agent"]["max_jobs_per_hour"]
        ):
            continue
        if working == "revising":
            for item in store.list("preview_changes"):
                if item["metadata"].get("revisions", 0) >= cfg["agent"]["max_revisions"]:
                    try:
                        store.update(
                            item["id"],
                            expected="preview_changes",
                            expected_version=item["updated_at"],
                            status="needs_human_review",
                        )
                    except LeaseLost:
                        continue
        candidate = store.claim(ready, working, cid=cid)
        if not candidate:
            continue
        job_id, token = candidate["id"], candidate["claim_token"]
        if working in MODEL_STAGES and not store.reserve_agent_call(
            cfg["agent"]["max_jobs_per_hour"], job_id, working
        ):
            store.update(job_id, expected=working, claim_token=token, status=ready)
            continue
        started = time.monotonic()
        try:
            with store.lease(candidate):
                _run_stage(cfg, store, studio, candidate)
            store.event(
                "stage_completed", {"stage": working, "seconds": round(time.monotonic() - started, 3)}, job_id
            )
        except LeaseLost:
            store.event("worker_lease_lost", {"stage": working}, job_id)
            return job_id
        except Exception as exc:
            meta = candidate["metadata"]
            retries = meta.setdefault("retries", {})
            retries[ready] = retries.get(ready, 0) + 1
            retrying = _retryable(exc) and retries[ready] <= cfg["agent"]["max_retries"]
            delay = min(300, 10 * 2 ** min(retries[ready] - 1, 5)) * random.uniform(0.8, 1.2)
            try:
                store.update(
                    job_id,
                    expected=working,
                    claim_token=token,
                    status=ready if retrying else "failed",
                    available_at=time.time() + delay if retrying else 0,
                    error=str(exc)[:1000],
                    metadata={**meta, "retry_state": ready},
                )
            except LeaseLost:
                store.event("worker_lease_lost", {"stage": working}, job_id)
                return job_id
            store.event(
                "job_retry_scheduled" if retrying else "job_failed",
                {"stage": working, "reason": str(exc)[:300], "attempt": retries[ready]},
                job_id,
            )
            if not retrying:
                raise
        return job_id
    return None


def retry(cfg, cid):
    store = Store(cfg)
    item = store.get(cid)
    if item["status"] != "failed":
        raise RelayError("Only failed editorial jobs can be retried here")
    state = item["metadata"].get("retry_state")
    if state not in {ready for ready, _ in STAGES}:
        raise RelayError("No safe retry state was recorded")
    store.update(cid, expected="failed", status=state, available_at=0, error=None)


def work(cfg, stop, *, watch=False, cid=None, stage=None):
    completed = []
    while not stop.is_set():
        if watch and cfg.get("config_path"):
            cfg = load(cfg["config_path"])
        if watch and not service_enabled(cfg, "editor"):
            stop.wait(2)
            continue
        try:
            job = step(cfg, cid=cid, stage=stage)
        except Exception as exc:
            if not watch:
                raise
            print(f"Worker paused one failed job: {exc}", flush=True)
            job = None
        if job:
            print(f"Processed {job}", flush=True)
            if not watch:
                completed.append(job)
        elif not watch:
            return completed
        else:
            stop.wait(3)
    return completed
