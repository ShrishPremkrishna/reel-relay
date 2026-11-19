"""Portable media backend implementing the worker's existing artifact contract."""

from __future__ import annotations

import fcntl
import hashlib
import json
import re
import shutil
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from .. import media
from ..config import Editing
from ..util import RelayError, TransientError, inside, read_json, sha256, write_json
from .captions import (
    caption_cues,
    corrected_words,
    finite,
    normalize_transcript,
    timeline_words,
    write_subtitles,
)
from .render import encode, technical_qa

Candidate = dict[str, Any]


class MediaBackend(Protocol):
    root: Path

    def ensure(self) -> None: ...
    def stage(self) -> Path: ...
    def catalog(self) -> list[dict[str, Any]]: ...
    def prepare(self, candidate: Candidate) -> tuple[Path, Path]: ...
    def render(self, candidate: Candidate) -> tuple[Path, Path, Path]: ...


@contextmanager
def project_lock(project):
    project.mkdir(parents=True, exist_ok=True)
    with (project / ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise TransientError(
                "Another media operation owns this project; retry after it finishes"
            ) from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class BuiltinStudio:
    """Offline transcriber and renderer; all artifacts stay beneath data_dir.

    Preparation and rendering accept the same candidate dictionaries used by the
    durable queue. A project stores relative media paths, not a user's checkout.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.data = Path(cfg["data_dir"]).resolve()
        self.root = self.data / "engine"
        self.settings = cfg.get("media", {})
        self.python = Path(self.settings.get("transcription_python") or self.root / "asr-env/bin/python")
        if not self.python.is_absolute():
            self.python = self.data / self.python

    def ensure(self):
        for path in (self.root / "projects", self.root / "assets", self.root / "models", self.root / "work"):
            path.mkdir(parents=True, exist_ok=True)

    def stage(self):
        self.ensure()
        return self.root

    def _path(self, value, default):
        path = Path(value or default)
        return inside(path if path.is_absolute() else self.data / path, self.data)

    def model_path(self):
        return self._path(self.settings.get("transcription_model"), self.root / "models/small")

    def readiness(self):
        return {
            "backend": "builtin",
            "transcription_runtime": self.python.is_file() and self._runtime_location_allowed(),
            "transcription_model": (self.model_path() / "model.bin").is_file(),
            "asset_catalog": self._path(
                self.settings.get("asset_catalog"), self.root / "assets/catalog.json"
            ).is_file(),
            "platform": sys.platform,
            "offline_boundary": sys.platform == "darwin"
            or bool(sys.platform.startswith("linux") and shutil.which("bwrap")),
        }

    def _runtime_location_allowed(self):
        # Resolve the environment directory, not its python symlink: a venv
        # inside data_dir legitimately points to a system Python binary.
        parent = self.python.parent.resolve()
        target = self.python.resolve()
        return all(
            path.is_relative_to(self.data) or not path.is_relative_to(Path.home())
            for path in (parent, target)
        )

    def _catalog_rows(self):
        catalog = self._path(self.settings.get("asset_catalog"), self.root / "assets/catalog.json")
        if not catalog.is_file():
            return []
        records = read_json(catalog)
        if not isinstance(records, list) or len(records) > 5000:
            raise RelayError("Asset catalog must be a bounded list")
        asset_root = self._path(self.settings.get("asset_root"), catalog.parent)
        rows = []
        for record in records:
            if not isinstance(record, dict) or record.get("kind") not in {"sfx", "meme", "broll", "music"}:
                continue
            relative = record.get("path")
            if not isinstance(relative, str):
                raise RelayError("Catalog asset path must be text")
            target = inside(asset_root / relative, self.data)
            if any(target.is_relative_to(self.data / name) for name in (".private", "secrets")):
                raise RelayError("Asset catalog cannot reference private account storage")
            if target.suffix.lower() not in {
                ".mp4",
                ".mov",
                ".webm",
                ".mkv",
                ".mp3",
                ".wav",
                ".ogg",
                ".flac",
                ".png",
                ".jpg",
                ".jpeg",
                ".webp",
            }:
                raise RelayError("Unsupported catalog media format")
            if not target.is_file():
                continue
            duration = record.get("duration")
            if duration is not None:
                duration = finite(duration, 0.001, 7200, "Catalog asset duration")
            rows.append(
                {
                    "path": str(target.relative_to(self.data)),
                    "kind": record["kind"],
                    "title": str(record.get("title") or target.stem)[:200],
                    "tags": [str(tag)[:60] for tag in record.get("tags", [])[:20]]
                    if isinstance(record.get("tags", []), list)
                    else [],
                    "duration": duration,
                    "rights": str(record.get("rights", "No usage rights recorded"))[:1000],
                    "source_url": str(record.get("source_url", ""))[:2000],
                    "usage_notes": str(record.get("usage_notes", ""))[:1000],
                    "attribution": str(record.get("attribution", ""))[:1000],
                    "sha256": record.get("sha256"),
                }
            )
        return rows

    def catalog(self):
        # Repeated utility-pack variants do not deserve most of the editor context.
        rows, groups = [], set()
        for row in reversed(self._catalog_rows()):
            if row["kind"] == "music":
                continue
            group = re.sub(r"[-_]\d{3,}.*$", "", row["path"])
            if group not in groups:
                groups.add(group)
                actual_hash = sha256(self.data / row["path"])
                if row["sha256"] and row["sha256"] != actual_hash:
                    raise RelayError(
                        "Asset differs from its catalog receipt; inspect and register the new file"
                    )
                row["sha256"] = actual_hash
                rows.append(row)
            if len(rows) >= 70:
                break
        return rows

    def _project(self, candidate):
        self.ensure()
        cid = candidate["id"]
        if not isinstance(cid, str) or not re.fullmatch(r"[a-f0-9]{16}", cid):
            raise RelayError("Invalid candidate identifier")
        return self.root / "projects" / cid

    def _manifest(self, project, candidate):
        source = inside(candidate["source_path"], self.data)
        if sha256(source) != candidate["source_hash"]:
            raise RelayError("The source changed after capture")
        path = project / "project.json"
        if path.is_file():
            value = read_json(path)
            if value.get("source_sha256") != candidate["source_hash"]:
                raise RelayError("Project belongs to a different source")
            return value
        result = media.probe(source, self.data)
        video = next((stream for stream in result["streams"] if stream.get("codec_type") == "video"), None)
        if not video:
            raise RelayError("Candidate has no video stream")
        value = {
            "schema_version": 1,
            "backend": "builtin",
            "candidate_id": candidate["id"],
            "source_path": str(source.relative_to(self.data)),
            "source_sha256": candidate["source_hash"],
            "media": {
                "duration": result["duration"],
                "width": video["width"],
                "height": video["height"],
                "has_audio": any(stream.get("codec_type") == "audio" for stream in result["streams"]),
            },
            "created_at": time.time(),
        }
        write_json(path, value)
        return value

    def _write_transcript(self, project, manifest, payload, provenance):
        transcript = normalize_transcript(payload, manifest["media"]["duration"], manifest["source_sha256"])
        transcript["provenance"] = str(provenance)[:1000]
        target = project / "transcript.json"
        if target.exists():
            archive = project / "transcript-history" / (str(time.time_ns()) + ".json")
            archive.parent.mkdir(exist_ok=True)
            shutil.copy2(target, archive)
        write_json(target, transcript)
        return transcript

    def import_transcript(self, candidate, payload, provenance="Operator supplied timed words"):
        project = self._project(candidate)
        with project_lock(project):
            manifest = self._manifest(project, candidate)
            self._write_transcript(project, manifest, payload, provenance)
        return project / "transcript.json"

    def _audio(self, project, manifest):
        if not manifest["media"]["has_audio"]:
            return None
        # Version the cache when alignment changes; never replace old evidence.
        target = project / "source-audio-v2.wav"
        if not target.exists():
            temporary = project / ("audio-" + uuid.uuid4().hex + ".wav")
            try:
                media.run(
                    [
                        media.binary("ffmpeg"),
                        "-v",
                        "error",
                        "-nostdin",
                        "-n",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        self.data / manifest["source_path"],
                        "-vn",
                        "-af",
                        f"aresample=16000:async=1:first_pts=0,apad,atrim=duration={manifest['media']['duration']}",
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-c:a",
                        "pcm_s16le",
                        temporary,
                    ],
                    self.data,
                )
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        return target

    def _asr_invocation(self, audio, output):
        self.ensure()
        audio, output = inside(audio, self.data), inside(output, self.data)
        model = self.model_path()
        if not self._runtime_location_allowed():
            raise RelayError(
                "Place the transcription environment inside data_dir; unrelated home environments are hidden by the media sandbox"
            )
        if not self.python.is_file() or not (model / "model.bin").is_file():
            raise RelayError(
                "Local transcription is not installed. Configure media.transcription_python and media.transcription_model, or import a timed transcript. See docs/MEDIA_ENGINE.md."
            )
        # Only an explicitly configured trusted interpreter executes the copied runner.
        runner_bytes = Path(__file__).with_name("asr_runner.py").read_bytes()
        runner_hash = hashlib.sha256(runner_bytes).hexdigest()
        runner = self.root / "work" / ("asr-" + runner_hash + ".py")
        if not runner.exists():
            temporary = self.root / "work" / (uuid.uuid4().hex + ".py")
            temporary.write_bytes(runner_bytes)
            temporary.replace(runner)
        if sha256(runner) != runner_hash:
            raise RelayError("The staged transcription runner changed; refusing to execute it")
        args = [self.python, "-B", runner, "--audio", audio, "--model", model, "--output", output]
        language = self.settings.get("transcription_language", "")
        if language:
            args.extend(["--language", language])
        return args, {
            "backend": "faster-whisper",
            "runtime_path": str(self.python),
            "model_path": str(model),
            "model": model.name,
            "runner_path": str(runner),
            "runner_sha256": runner_hash,
            "language_requested": language or None,
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": True,
            "network": "denied by media subprocess sandbox",
        }

    def transcribe_audio(self, audio, folder, duration, source_hash):
        """Transcribe canonical PCM into a new receipt directory, retaining raw ASR.

        This public helper does not mutate source projects or completed exports.
        The caller supplies a unique writable folder beneath data_dir.
        """
        folder = inside(folder, self.data)
        folder.mkdir(parents=True, exist_ok=True)
        audio = inside(audio, self.data)
        duration = finite(duration, 0.001, 7200, "Transcription duration")
        output = folder / ("asr-raw-" + uuid.uuid4().hex + ".json")
        args, provenance = self._asr_invocation(audio, output)
        model = self.model_path()
        model_files = {
            path.name: sha256(inside(path, self.data))
            for path in sorted(model.iterdir())
            if path.is_file() and path.suffix in {".bin", ".json", ".txt"}
        }
        audio_hash = sha256(audio)
        started = time.monotonic()
        media.run(args, self.data, timeout=1800)
        if sha256(audio) != audio_hash:
            raise RelayError("ASR input changed during transcription")
        if sha256(provenance["runner_path"]) != provenance["runner_sha256"] or model_files != {
            path.name: sha256(inside(path, self.data))
            for path in sorted(model.iterdir())
            if path.is_file() and path.suffix in {".bin", ".json", ".txt"}
        }:
            raise RelayError("ASR model or runner changed during transcription")
        if output.stat().st_size > 8 * 1024**2:
            raise RelayError("ASR response exceeds its bounded transcript size")
        transcript = normalize_transcript(read_json(output), duration, source_hash)
        transcript["provenance"] = "Offline ASR of canonical PCM; machine recognition, not a listening review"
        return transcript, {
            **provenance,
            "model_file_sha256": model_files,
            "audio_sha256": audio_hash,
            "raw_path": str(output),
            "raw_sha256": sha256(output),
            "wall_seconds": round(time.monotonic() - started, 3),
        }

    def _transcribe(self, project, manifest, audio):
        output = project / ("asr-" + uuid.uuid4().hex + ".json")
        args, _ = self._asr_invocation(audio, output)
        try:
            media.run(args, self.data, timeout=1800)
            return self._write_transcript(
                project,
                manifest,
                read_json(output),
                "Local faster-whisper; no audio upload or listening review",
            )
        finally:
            output.unlink(missing_ok=True)

    def prepare(self, candidate):
        project = self._project(candidate)
        with project_lock(project):
            manifest = self._manifest(project, candidate)
            audio = self._audio(project, manifest)
            override = candidate.get("metadata", {}).get("transcript_override_path")
            if override:
                payload = read_json(self._path(override, ""))
                digest = sha256(self._path(override, ""))
                old = (
                    read_json(project / "transcript.json") if (project / "transcript.json").is_file() else {}
                )
                if old.get("override_hash") != digest:
                    transcript = self._write_transcript(
                        project, manifest, payload, "Explicit transcript override"
                    )
                    transcript["override_hash"] = digest
                    write_json(project / "transcript.json", transcript)
            if not (project / "transcript.json").is_file():
                if audio:
                    self._transcribe(project, manifest, audio)
                else:
                    self._write_transcript(
                        project,
                        manifest,
                        {"backend": "silent-source", "words": []},
                        "Source contains no audio stream",
                    )
            transcript = normalize_transcript(
                read_json(project / "transcript.json"),
                manifest["media"]["duration"],
                manifest["source_sha256"],
            )
            evidence = self.data / "evidence" / candidate["id"] / ("prepare-" + uuid.uuid4().hex)
            evidence.mkdir(parents=True, exist_ok=True)
            images = media.contact_sheets(
                self.data / manifest["source_path"],
                evidence,
                self.data,
                manifest["media"]["duration"],
                prefix="builtin-source",
            )
            shutil.copy2(images[0], evidence / "source-0.jpg")
            packet = {
                "stage": "select",
                "candidate_id": candidate["id"],
                "channel": candidate["channel"],
                "artifact_root": str(self.data),
                "duration": manifest["media"]["duration"],
                "source_dimensions": [manifest["media"]["width"], manifest["media"]["height"]],
                "source_path": str(self.data / manifest["source_path"]),
                "source_sha256": manifest["source_sha256"],
                "editing": Editing.model_validate(self.cfg.get("editing", {})).model_dump(),
                "transcript": transcript,
                "images": [str(image) for image in images],
                "audio_path": str(audio) if audio else None,
                "project_manifest": str(project / "project.json"),
                "assets": self.catalog(),
                "chat": candidate.get("metadata", {}).get("trigger", {}),
                "attribution": candidate.get("attribution", ""),
                "is_test": bool(candidate.get("is_test")),
                "sampling": "Timestamped still frames at roughly two-second intervals; separate perception adapters may add motion/audio evidence.",
                "audio_review": "Local ASR transcript only; no listening review has occurred.",
            }
            preferences = self._path(
                self.settings.get("editorial_preferences"), self.root / "preferences.json"
            )
            if preferences.is_file():
                packet["editorial_preferences"] = read_json(preferences)
            write_json(evidence / "evidence.json", packet)
        return project, evidence / "evidence.json"

    def _assets(self, decision, evidence):
        requested = {item["asset_path"] for key in ("sfx", "overlays") for item in decision.get(key, [])}
        catalog = {row["path"]: row for row in self._catalog_rows()}
        accepted = {row["path"]: row for row in evidence.get("assets", [])}
        result = {}
        for name in requested:
            if name not in accepted or name not in catalog:
                raise RelayError("Effect was not in the editor's catalog")
            row = catalog[name]
            path = inside(self.data / name, self.data)
            digest = sha256(path)
            if any(
                expected and expected != digest
                for expected in (row.get("sha256"), accepted[name].get("sha256"))
            ):
                raise RelayError("An effect changed after editorial selection")
            info = json.loads(
                media.run(
                    [
                        media.binary("ffprobe"),
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-show_format",
                        "-show_streams",
                        "-of",
                        "json",
                        path,
                    ],
                    self.data,
                    timeout=60,
                )
            )
            try:
                duration = float(info.get("format", {}).get("duration", row.get("duration") or 90))
            except (ValueError, TypeError) as exc:
                raise RelayError("Asset duration is invalid") from exc
            if not 0 < duration <= 7200:
                raise RelayError("Asset duration is invalid")
            result[name] = {
                **row,
                "duration": duration,
                "sha256": digest,
                "resolved_path": str(path),
                "has_audio": any(stream.get("codec_type") == "audio" for stream in info["streams"]),
                "has_video": any(stream.get("codec_type") == "video" for stream in info["streams"]),
            }
        return result

    def render(self, candidate):
        project = self._project(candidate)
        with project_lock(project):
            manifest = self._manifest(project, candidate)
            evidence_path = inside(candidate["evidence_path"], self.data)
            evidence = read_json(evidence_path)
            # The decision was made against this immutable evidence packet, not
            # against whichever transcript cache happens to be newest now.
            transcript = normalize_transcript(
                evidence["transcript"], manifest["media"]["duration"], manifest["source_sha256"]
            )
            preferences = Editing.model_validate(
                candidate.get("metadata", {}).get("editing_settings", self.cfg.get("editing", {}))
            ).model_dump()
            # Public callers cannot skip the same bounded editorial contract as queue workers.
            from ..agent import validate_decision

            decision = validate_decision(candidate["decision"], evidence, editing=preferences)
            assets = self._assets(decision, evidence)
            corrections = decision.get("caption_corrections", [])
            words = timeline_words(
                corrected_words(transcript, corrections, manifest["media"]["duration"]), decision["keep"]
            )
            cues = caption_cues(
                words,
                maximum_words=preferences["caption_words"],
                maximum_characters=max(28, preferences["caption_words"] * 8 + 4),
            )
            duration = sum(cut["end"] - cut["start"] for cut in decision["keep"])
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
            export = project / "exports" / stamp
            export.mkdir(parents=True, exist_ok=False)
            try:
                # Catalog files may be refreshed while another clip renders. Each
                # attempt consumes its own checked copy, never a mutable library path.
                for index, row in enumerate(assets.values()):
                    original = Path(row["resolved_path"])
                    frozen = export / "inputs" / (f"asset-{index}-" + row["sha256"] + original.suffix.lower())
                    frozen.parent.mkdir(exist_ok=True)
                    shutil.copyfile(original, frozen)
                    if sha256(frozen) != row["sha256"]:
                        raise RelayError("An effect changed while its render input was being frozen")
                    row["resolved_path"] = str(frozen)
                write_json(export / "edit.json", decision)
                write_json(export / "editing.json", preferences)
                write_json(export / "transcript.json", transcript)
                write_json(
                    export / "captions.json",
                    {
                        "cues": cues,
                        "corrections": corrections,
                        "enabled": preferences["captions_enabled"],
                        "maximum_words": preferences["caption_words"],
                    },
                )
                write_subtitles(
                    export,
                    cues,
                    decision.get("hook", ""),
                    duration,
                    render_captions=preferences["captions_enabled"],
                )
                record_assets = [
                    {key: value for key, value in row.items() if key != "resolved_path"}
                    for row in assets.values()
                ]
                write_json(export / "assets.json", record_assets)
                credits = [candidate.get("attribution", "").strip()]
                credits.extend(row["attribution"].strip() for row in assets.values())
                (export / "credits.txt").write_text(
                    "\n\n".join(dict.fromkeys(credit for credit in credits if credit)) + "\n"
                )
                (export / "editorial-notes.txt").write_text(
                    decision.get("reason", "")
                    + "\n\n"
                    + "\n".join(
                        str(item.get("reason", ""))
                        for key in ("sfx", "overlays")
                        for item in decision.get(key, [])
                    )
                )
                encode(
                    self.data / manifest["source_path"],
                    manifest["media"],
                    decision,
                    assets,
                    export / "captions.ass",
                    export / "reel.mp4",
                    self.data,
                )
                if sha256(self.data / manifest["source_path"]) != manifest["source_sha256"]:
                    raise RelayError("Source changed during rendering; this attempt cannot be published")
                qa = technical_qa(export / "reel.mp4", self.data, duration)
                write_json(export / "qa.json", qa)
                if qa["warnings"]:
                    raise RelayError("Export failed its technical audio checks")
                media.run(
                    [
                        media.binary("ffmpeg"),
                        "-v",
                        "error",
                        "-nostdin",
                        "-n",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-ss",
                        str(min(0.5, duration / 2)),
                        "-i",
                        export / "reel.mp4",
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=540:960",
                        export / "poster.jpg",
                    ],
                    self.data,
                )
                interval = max(0.5, duration / 12)
                graph = f"fps=1/{interval},scale=270:480,tile=4x3"
                media.run(
                    [
                        media.binary("ffmpeg"),
                        "-v",
                        "error",
                        "-nostdin",
                        "-n",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        export / "reel.mp4",
                        "-vf",
                        graph,
                        "-frames:v",
                        "1",
                        export / "contact-sheet.jpg",
                    ],
                    self.data,
                )
                output_hash = sha256(export / "reel.mp4")
                export_manifest = {
                    "schema_version": 1,
                    "backend": "builtin",
                    "source_sha256": manifest["source_sha256"],
                    "output_sha256": output_hash,
                    "timeline_sha256": sha256(export / "edit.json"),
                    "editing_sha256": sha256(export / "editing.json"),
                    "transcript_sha256": sha256(export / "transcript.json"),
                    "selection_evidence_sha256": sha256(evidence_path),
                    "asset_hashes": {name: row["sha256"] for name, row in assets.items()},
                    "duration": duration,
                    "output": {"width": 1080, "height": 1920, "fps": 30},
                    "complete": True,
                }
                packet = {
                    "stage": "review",
                    "candidate_id": candidate["id"],
                    "artifact_root": str(self.data),
                    "images": [str(export / "contact-sheet.jpg")],
                    "decision": decision,
                    "editing": preferences,
                    "qa": qa,
                    "duration": duration,
                    "video_path": str(export / "reel.mp4"),
                    "output_sha256": output_hash,
                    "audio_review": "Full decode and loudness measured; not a listening review.",
                    "is_test": bool(candidate.get("is_test")),
                }
                review_path = export / "review-evidence.json"
                write_json(review_path, packet)
                export_manifest["review_evidence_sha256"] = sha256(review_path)
                export_manifest["artifact_sha256"] = {
                    str(path.relative_to(export)): sha256(path)
                    for path in sorted(export.rglob("*"))
                    if path.is_file()
                }
                # Completion is the final write; failures never advertise a ready export.
                write_json(export / "manifest.json", export_manifest)
            except Exception as exc:
                write_json(
                    export / "failure.json",
                    {"type": type(exc).__name__, "message": str(exc)[:1000], "complete": False},
                )
                raise
        return project, export, review_path
