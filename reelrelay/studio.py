from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import media
from .store import Store
from .util import RelayError, inside, read_json, sha256, write_json


def Studio(cfg):
    """Select an engine through a stable, provider-independent worker contract."""
    if cfg.get("media", {}).get("engine", "builtin") == "builtin":
        from .engine import BuiltinStudio

        return BuiltinStudio(cfg)
    return LegacyStudio(cfg)


def clone_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if not destination.exists():
            destination.symlink_to(os.readlink(source))
        return
    if (
        destination.is_file()
        and source.stat().st_size == destination.stat().st_size
        and source.stat().st_mtime_ns == destination.stat().st_mtime_ns
    ):
        return
    destination.unlink(missing_ok=True)
    cloned = False
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        cloned = lib.clonefile(os.fsencode(source), os.fsencode(destination), 0) == 0
    if not cloned:
        shutil.copyfile(source, destination)
    shutil.copystat(source, destination)


class LegacyStudio:
    def __init__(self, cfg):
        self.cfg = cfg
        self.original = Path(cfg["reel_studio"])
        self.root = Path(cfg["data_dir"]) / "studio"
        self.python = self.root / ".venv/bin/python"

    def stage(self):
        if not (self.original / "reelstudio/cli.py").is_file():
            raise RelayError("Set reel_studio to your existing Reel Studio installation")
        # A private runtime snapshot, never redistributed with the MIT application.
        # APFS clonefile avoids duplicating hundreds of MB of model/runtime data.
        with Store(self.cfg).lock("studio-setup", seconds=600):
            for directory in ("reelstudio", "scripts", "security", "library", "models", ".venv"):
                source = self.original / directory
                if not source.is_dir():
                    raise RelayError(f"Reel Studio is missing {directory}")
                for base, dirs, files in os.walk(source, followlinks=False):
                    dirs[:] = [d for d in dirs if d not in ("__pycache__", ".cache")]
                    for name in files:
                        src = Path(base) / name
                        clone_file(src, self.root / src.relative_to(self.original))
                    for name in list(dirs):
                        src = Path(base) / name
                        if src.is_symlink():
                            dst = self.root / src.relative_to(self.original)
                            dst.parent.mkdir(parents=True, exist_ok=True)
                            if not dst.exists():
                                dst.symlink_to(os.readlink(src))
                            dirs.remove(name)
            (self.root / "projects").mkdir(exist_ok=True)
            write_json(
                self.root / "snapshot.json",
                {
                    "source": str(self.original),
                    "engine_sha256": sha256(self.original / "reelstudio/core.py"),
                    "catalog_sha256": sha256(self.original / "library/catalog.json"),
                },
            )
        return self.root

    def ensure(self):
        if not (self.root / "snapshot.json").exists():
            self.stage()

    def call(self, *args, timeout=3600):
        self.ensure()
        env = media.environment(self.root)
        result = subprocess.run(
            media.command(
                [str(self.python), "-B", str(self.root / "scripts/entry.py"), *map(str, args)], self.root
            ),
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode:
            raise RelayError(f"Reel Studio {args[0]} failed: {(result.stderr or result.stdout)[-3000:]}")
        return result.stdout

    def catalog(self):
        self.ensure()
        rows = read_json(self.root / "library/catalog.json")
        # Prefer newly added effects; collapse numbered utility-pack variants to
        # avoid spending a large model context on nearly identical noises.
        chosen, groups = [], set()
        for asset in reversed(rows):
            if asset["kind"] not in ("sfx", "meme", "broll"):
                continue
            group = re.sub(r"[-_]\d{3,}.*$", "", asset["path"])
            if group in groups:
                continue
            groups.add(group)
            chosen.append(asset)
            if len(chosen) >= 70:
                break
        return [
            {
                k: a.get(k)
                for k in ("path", "kind", "tags", "title", "duration", "rights", "usage_notes", "attribution")
            }
            for a in chosen
        ]

    def prepare(self, candidate):
        if sha256(candidate["source_path"]) != candidate["source_hash"]:
            raise RelayError("The source changed after capture; import the new version separately")
        self.ensure()
        name = "relay-" + candidate["id"]
        project = self.root / "projects" / name
        if not (project / "project.json").exists():
            self.call(
                "init",
                name,
                "--source",
                candidate["source_path"],
                "--topic",
                f"{candidate['channel']} live highlight",
            )
        self.call("inspect", name)
        self.call("transcribe", name)
        meta = read_json(project / "project.json")
        inventory = read_json(project / "inventory.json")
        transcripts = read_json(project / "transcript.json")
        first = meta["clips"][0]
        packet_dir = Path(self.cfg["data_dir"]) / "evidence" / candidate["id"]
        packet_dir.mkdir(parents=True, exist_ok=True)
        images = []
        for index, item in enumerate(inventory):
            destination = packet_dir / f"source-{index}.jpg"
            shutil.copy2(project / item["contact_sheet"], destination)
            images.append(str(destination))
        detailed = media.contact_sheets(
            candidate["source_path"], packet_dir, self.cfg["data_dir"], first["media"]["duration"]
        )
        if detailed:
            images = [str(p) for p in detailed]
        packet = {
            "stage": "select",
            "candidate_id": candidate["id"],
            "channel": candidate["channel"],
            "artifact_root": self.cfg["data_dir"],
            "duration": first["media"]["duration"],
            "source_dimensions": [first["media"]["width"], first["media"]["height"]],
            "transcript": transcripts.get("clips", {}).get(first["id"], {}),
            "chat": candidate["metadata"].get("trigger", {}),
            "images": images,
            "assets": self.catalog(),
            "attribution": candidate["attribution"],
            "is_test": bool(candidate["is_test"]),
            "sampling": "Timestamped frames roughly every two seconds, capped at 60 frames. Motion/audio cannot be fully assessed from stills.",
            "audio_review": "Transcript and measured audio only; no listening review has occurred.",
        }
        preferences = self.original / "library/preferences.json"
        if preferences.is_file():
            packet["editorial_preferences"] = read_json(preferences)
        write_json(packet_dir / "evidence.json", packet)
        return project, packet_dir / "evidence.json"

    def _layout(self, candidate, decision, original_project):
        if decision.get("layout") != "split":
            return original_project
        meta = read_json(original_project / "project.json")
        clip = meta["clips"][0]
        source = inside(original_project / clip["source"], self.root)
        w, h = clip["media"]["width"], clip["media"]["height"]
        rect = decision["facecam"]
        x, y = int(rect["x"] * w) // 2 * 2, int(rect["y"] * h) // 2 * 2
        cw, ch = max(2, int(rect["width"] * w) // 2 * 2), max(2, int(rect["height"] * h) // 2 * 2)
        layout_hash = hashlib.sha256(json.dumps(rect, sort_keys=True).encode()).hexdigest()[:10]
        output = self.root / "layouts" / (candidate["id"] + "-" + layout_hash + ".mp4")
        output.parent.mkdir(exist_ok=True)
        if not output.exists():
            graph = (
                f"[0:v]split=3[face][game][bg];[face]crop={cw}:{ch}:{x}:{y},"
                "scale=1080:540:force_original_aspect_ratio=increase,crop=1080:540,setsar=1[top];"
                "[bg]scale=1080:1380:force_original_aspect_ratio=increase,crop=1080:1380,boxblur=30:2[back];"
                "[game]scale=1080:1380:force_original_aspect_ratio=decrease,setsar=1[front];"
                "[back][front]overlay=(W-w)/2:(H-h)/2[bottom];[top][bottom]vstack=inputs=2[out]"
            )
            media.render_atomic(
                [
                    media.binary("ffmpeg"),
                    "-v",
                    "error",
                    "-nostdin",
                    "-n",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    source,
                    "-filter_complex",
                    graph,
                    "-map",
                    "[out]",
                    "-map",
                    "0:a:0?",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "veryfast",
                    "-crf",
                    "21",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                ],
                output,
                self.root,
                timeout=600,
                expected_duration=clip["media"]["duration"],
            )
        name = original_project.name + "-layout-" + layout_hash
        project = self.root / "projects" / name
        if not (project / "project.json").exists():
            self.call("init", name, "--source", output, "--topic", decision["reason"][:200])
        target_meta = read_json(project / "project.json")
        target_id = target_meta["clips"][0]["id"]
        if not (project / "transcripts" / (target_id + ".json")).exists():
            source_transcript = read_json(original_project / "transcript.json")["clips"][clip["id"]]
            transfer = project / "work" / "transfer-transcript.json"
            write_json(transfer, source_transcript)
            self.call("import-transcript", name, transfer, "--clip-id", target_id)
        return project

    def render(self, candidate):
        with Store(self.cfg).lock("legacy-render-" + candidate["id"], seconds=600):
            return self._render(candidate)

    def _render(self, candidate):
        decision = candidate["decision"]
        original = inside(self.root / "projects" / ("relay-" + candidate["id"]), self.root)
        project = self._layout(candidate, decision, original)
        meta = read_json(project / "project.json")
        clip_id = meta["clips"][0]["id"]
        keep = [
            {
                "clip_id": clip_id,
                "start": c["start"],
                "end": c["end"],
                "reason": c["reason"],
                "focus_x": c["focus_x"],
                "focus_y": c["focus_y"],
                "zoom": c["zoom"],
                "fit": "contain" if decision["layout"] == "contain" else "cover",
            }
            for c in decision["keep"]
        ]
        plan = {
            "schema_version": 2,
            "source_sha256": meta["source_sha256"],
            "keep": keep,
            "captions": {"enabled": True, "words_per_cue": 3, "font_size": 64, "bottom_margin": 330},
            "hook": {"text": decision["hook"][:80], "duration": 2.5},
            "cta": {"text": "", "duration": 1},
            "music": None,
            "sfx": [
                {"path": a["asset_path"], "at": a["at"], "duration": a["duration"], "gain_db": a["gain_db"]}
                for a in decision["sfx"]
            ],
            "broll": [
                {
                    "path": a["asset_path"],
                    "at": a["at"],
                    "duration": a["duration"],
                    "source_start": a["source_start"],
                    "fit": "contain",
                    "audio": False,
                    "width_fraction": a["width_fraction"],
                    "height_fraction": a["height_fraction"],
                    "x": a["x"],
                    "y": a["y"],
                }
                for a in decision["overlays"]
            ],
            "output": {"width": 1080, "height": 1920, "fps": 30},
        }
        write_json(project / "edit.json", plan)
        (project / "editorial-notes.md").write_text(
            decision["reason"]
            + "\n\n"
            + "\n".join(f"Effect: {a['reason']}" for a in decision["sfx"] + decision["overlays"])
        )
        self.call("validate", project.name)
        self.call("render", project.name)
        export = inside(project / read_json(project / "latest-export.json")["path"], self.root)
        qa = read_json(export / "qa.json")
        if qa.get("full_decode") != "passed" or qa.get("warnings"):
            raise RelayError("Export has a technical QA problem; review it before posting")
        image = export / "contact-sheet.jpg"
        packet = {
            "stage": "review",
            "candidate_id": candidate["id"],
            "images": [str(image)],
            "artifact_root": self.cfg["data_dir"],
            "decision": decision,
            "qa": qa,
            "duration": qa["expected_duration"],
            "audio_review": "Full decode and loudness measured; not a listening review.",
            "is_test": bool(candidate["is_test"]),
        }
        write_json(export / "review-evidence.json", packet)
        return project, export, export / "review-evidence.json"
