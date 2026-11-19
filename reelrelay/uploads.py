"""Bounded browser imports into the local, offline media boundary."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from . import media
from .store import Store
from .util import RelayError, inside, read_json, sha256, write_json

MAX_UPLOAD_BYTES = 100 * 1024**2
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
ASSET_EXTENSIONS = VIDEO_EXTENSIONS | {".mp3", ".wav", ".ogg", ".flac", ".png", ".jpg", ".jpeg", ".webp"}


async def receive_upload(request, cfg, filename, *, asset=False):
    extension = Path(filename).suffix.lower()
    if extension not in (ASSET_EXTENSIONS if asset else VIDEO_EXTENSIONS):
        raise RelayError("Choose a supported media file")
    folder = inside(Path(cfg["data_dir"]) / "ingest", cfg["data_dir"])
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (uuid.uuid4().hex + extension)
    total = 0
    try:
        with target.open("xb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise RelayError("Browser imports must be 100 MB or smaller")
                output.write(chunk)
        if total == 0:
            raise RelayError("Choose a nonempty media file")
        return target
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def add_asset(cfg, source, *, title, kind, rights, attribution, tags):
    if cfg["media"]["engine"] != "builtin":
        raise RelayError("Browser asset uploads require the builtin media engine")
    # Probe using the same credential-free, network-denied subprocess as rendering.
    raw = media.run(
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
            source,
        ],
        cfg["data_dir"],
        60,
    )
    try:
        info = json.loads(raw)
        duration_value = info.get("format", {}).get("duration", 0)
        duration = 0 if duration_value in (None, "N/A") else float(duration_value)
    except (ValueError, TypeError, AttributeError):
        raise RelayError("Could not read the asset's media information") from None
    types = {stream.get("codec_type") for stream in info.get("streams", [])}
    if (kind == "sfx" and "audio" not in types) or (kind != "sfx" and "video" not in types):
        raise RelayError("Sound effects need audio; memes and b-roll need an image or video")
    if duration < 0 or duration > 7200 or not duration < float("inf"):
        raise RelayError("Asset duration must be under two hours")
    data = Path(cfg["data_dir"])
    catalog = inside(data / (cfg["media"]["asset_catalog"] or "engine/assets/catalog.json"), data)
    root = inside(data / cfg["media"]["asset_root"], data) if cfg["media"]["asset_root"] else catalog.parent
    for path in (catalog, root):
        if any(path.is_relative_to(data / name) for name in (".private", "secrets")):
            raise RelayError("The asset library cannot use private account storage")
    digest = sha256(source)
    destination = inside(root / "uploads" / (digest + source.suffix.lower()), data)
    record = {
        "path": str(destination.relative_to(root)),
        "kind": kind,
        "title": title,
        "rights": rights,
        "attribution": attribution,
        "tags": tags,
        "sha256": digest,
    }
    if duration:
        record["duration"] = duration
    with Store(cfg).lock("asset-catalog", seconds=90):
        records = read_json(catalog) if catalog.is_file() else []
        if not isinstance(records, list) or len(records) >= 5000:
            raise RelayError("The asset catalog must be a list of fewer than 5,000 assets")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if sha256(destination) != digest:
                raise RelayError("An existing asset failed its integrity check") from None
        else:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
                    temporary = Path(output.name)
                    with source.open("rb") as incoming:
                        shutil.copyfileobj(incoming, output)
                    output.flush()
                    os.fsync(output.fileno())
                temporary.replace(destination)
            except OSError:
                raise RelayError(
                    "Could not save this asset. Check library disk space and permissions."
                ) from None
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        # A repeated upload updates metadata without duplicating immutable bytes.
        records = [row for row in records if row.get("path") != record["path"]]
        records.append(record)
        write_json(catalog, records)
    asset_id = hashlib.sha256(str(destination.relative_to(data)).encode()).hexdigest()[:24]
    return {"ok": True, "id": asset_id}
