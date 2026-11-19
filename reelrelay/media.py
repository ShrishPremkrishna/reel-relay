from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from itertools import pairwise
from pathlib import Path

from .util import RelayError, inside


def binary(name):
    for path in (
        Path("/opt/homebrew/opt/ffmpeg-full/bin") / name,
        Path("/usr/local/opt/ffmpeg-full/bin") / name,
    ):
        if path.is_file():
            return str(path)
    path = shutil.which(name)
    if not path:
        raise RelayError(f"Install {name} from its official package manager first")
    return path


def environment(root):
    cache = Path(root) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT")}
    env.update(
        PYTHONDONTWRITEBYTECODE="1",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        XDG_CACHE_HOME=str(cache),
        TMPDIR=str(cache),
    )
    return env


def command(args, root):
    root = Path(root).resolve()
    if sys.platform == "darwin":
        # A host sandbox denying network/writes does not prove its read boundary.
        # Always install our own profile; fail closed when nested profiles are
        # disallowed, rather than exposing credentials to a media decoder.
        root.mkdir(parents=True, exist_ok=True)
        private = root / ".private"
        private.mkdir(mode=0o700, exist_ok=True)
        profile = private / "media.sb"
        policy = """(version 1)
(allow default)
(deny network*)
(deny file-read-data (subpath (string-append (param "DATA_ROOT") "/.private")))
(deny file-read-data (subpath (string-append (param "DATA_ROOT") "/secrets")))
(deny file-write* (subpath (string-append (param "DATA_ROOT") "/.private")))
(deny file-write* (subpath (string-append (param "DATA_ROOT") "/secrets")))
(deny file-read-data file-write* (regex #"/[^/]*[.]sqlite3?(-wal|-shm|-journal)?$"))
(deny file-read-data file-write* (regex #"/[^/]*[.]toml$"))
(deny file-write* (require-all
 (require-not (subpath (param "DATA_ROOT")))
 (require-not (literal "/dev/null"))) )
(deny file-read-data (require-all
 (subpath (param "USER_HOME"))
 (require-not (subpath (param "DATA_ROOT")))
 (require-not (subpath (string-append (param "USER_HOME") "/Library/Fonts")))))
"""
        if not profile.is_file() or profile.read_text() != policy:
            with tempfile.NamedTemporaryFile("w", dir=private, delete=False) as stream:
                stream.write(policy)
                temporary = Path(stream.name)
            temporary.replace(profile)
        return [
            "/usr/bin/sandbox-exec",
            "-f",
            str(profile),
            "-D",
            f"DATA_ROOT={root}",
            "-D",
            f"USER_HOME={Path.home()}",
            *map(str, args),
        ]
    # bubblewrap is an explicit offline boundary for Linux media processing.
    if sys.platform.startswith("linux") and shutil.which("bwrap"):
        writable = []
        # Reveal only controller-created artifact directories. Masking the root
        # also hides SQLite sidecars created after the decoder has started.
        for path in root.iterdir():
            if path.is_dir() and not path.is_symlink() and path.name not in {".private", "secrets"}:
                writable.extend(["--bind", str(path), str(path)])
        return [
            "bwrap",
            "--unshare-net",
            "--die-with-parent",
            "--ro-bind",
            "/",
            "/",
            "--tmpfs",
            str(Path.home()),
            "--tmpfs",
            str(root),
            *writable,
            "--remount-ro",
            str(root),
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            *map(str, args),
        ]
    raise RelayError("Offline media processing needs macOS sandbox-exec or Linux bubblewrap")


def run(args, root, timeout=300):
    # Create writable cache directories before constructing Linux bind mounts.
    env = environment(root)
    result = subprocess.run(
        command(args, root),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=root,
        check=False,
    )
    if result.returncode:
        if "sandbox_apply" in result.stderr or "sandbox initialization failed" in result.stderr:
            raise RelayError(
                "The host blocked the media sandbox. Start Relay from your terminal so its "
                "media child can apply the offline profile; no unsandboxed fallback is used."
            )
        raise RelayError(f"Media operation failed ({result.returncode}): {result.stderr[-2500:]}")
    return result.stdout


def probe(path, root):
    path = inside(path, root)
    if not path.is_file() or path.suffix.lower() not in {".mp4", ".ts", ".mkv", ".mov", ".webm", ".wav"}:
        raise RelayError("Expected a local media file")
    value = json.loads(
        run(
            [
                binary("ffprobe"),
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
            root,
            60,
        )
    )
    duration = float(value.get("format", {}).get("duration", 0))
    if not 0 < duration <= 7200:
        raise RelayError("Source duration is missing or exceeds two hours")
    return {"duration": duration, "streams": value.get("streams", [])}


def render_atomic(args, output, root, *, timeout=300, expected_duration=None):
    """Publish a video filename only after encoding and probing succeed."""
    output = inside(output, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RelayError("Refusing to overwrite existing media")
    with tempfile.TemporaryDirectory(dir=output.parent, prefix="encoding-") as temporary:
        staged = Path(temporary) / output.name
        run([*args, staged], root, timeout)
        info = probe(staged, root)
        if not any(stream.get("codec_type") == "video" for stream in info["streams"]):
            raise RelayError("Encoded candidate has no video stream")
        if expected_duration is not None and abs(info["duration"] - expected_duration) > max(
            0.5, expected_duration * 0.01
        ):
            raise RelayError("Encoded candidate duration does not match its recording window")
        # Same-filesystem, atomic, and fails if another worker already created it.
        os.link(staged, output)
    return info


def detail_contact_sheets(source, folder, root, windows):
    """Bounded, timestamped 4-fps evidence for a specific editorial uncertainty."""
    source, folder = inside(source, root), inside(folder, root)
    if not isinstance(windows, list) or not 1 <= len(windows) <= 2:
        raise RelayError("Detailed inspection requires one or two short windows")
    duration = probe(source, root)["duration"]
    for window in windows:
        start, end = window.get("start"), window.get("end")
        if (
            any(
                isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                for x in (start, end)
            )
            or not 0 <= start < end <= duration
            or not 1 <= end - start <= 10
        ):
            raise RelayError("Detailed inspection windows must span 1–10 source seconds")
    folder.mkdir(parents=True, exist_ok=False)
    images = []
    for index, window in enumerate(windows):
        start, end = window["start"], window["end"]
        graph = (
            f"select='gte(t,{start})*lt(t,{end})*(isnan(prev_selected_t)+gte(t-prev_selected_t,0.25))',"
            "scale=480:270:force_original_aspect_ratio=decrease,"
            "pad=480:300:(ow-iw)/2:30+(270-ih)/2:color=black,"
            "drawtext=text='%{pts\\:hms}':fontsize=20:fontcolor=white:x=8:y=4,tile=3x4"
        )
        run(
            [
                binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-n",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                source,
                "-vf",
                graph,
                "-fps_mode",
                "vfr",
                "-frames:v",
                str(math.ceil((end - start) * 4 / 12)),
                folder / f"window-{index:02d}-%02d.jpg",
            ],
            root,
        )
        found = sorted(folder.glob(f"window-{index:02d}-*.jpg"))
        if not found:
            raise RelayError("No frames decoded for the requested inspection window")
        images.extend(found)
    return images


def contact_sheets(source, folder, root, duration, *, prefix="detail"):
    """At most 60 timestamped frames, with a two-second sampling target."""
    source, folder = inside(source, root), inside(folder, root)
    folder.mkdir(parents=True, exist_ok=True)
    interval = max(2, duration / 60)
    existing = sorted(folder.glob(prefix + "-*.jpg"))
    marker = folder / (prefix + "-complete.json")
    if existing and marker.exists():
        return existing
    graph = (
        f"select='isnan(prev_selected_t)+gte(t-prev_selected_t,{interval})',"
        "scale=480:270:force_original_aspect_ratio=decrease,"
        "pad=480:300:(ow-iw)/2:30+(270-ih)/2:color=black,"
        "drawtext=text='%{pts\\:hms}':fontsize=20:fontcolor=white:x=8:y=4,tile=3x4"
    )
    with tempfile.TemporaryDirectory(dir=folder, prefix="frames-") as temporary:
        run(
            [
                binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-n",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                source,
                "-vf",
                graph,
                "-fps_mode",
                "vfr",
                "-frames:v",
                "5",
                Path(temporary) / (prefix + "-%02d.jpg"),
            ],
            root,
        )
        frames = sorted(Path(temporary).glob(prefix + "-*.jpg"))
        if not frames:
            raise RelayError("No contact-sheet frames could be extracted")
        for path in existing:
            path.unlink()
        for frame in frames:
            frame.replace(folder / frame.name)
        from .util import write_json

        write_json(marker, {"complete": True, "count": len(frames)})
    return sorted(folder.glob(prefix + "-*.jpg"))


def extract(segments, start, end, output, root):
    selected = [s for s in segments if s["end"] > start and s["start"] < end]
    if not selected:
        raise RelayError("The recording no longer contains that time window")
    actual_start, actual_end = max(start, selected[0]["start"]), min(end, selected[-1]["end"])
    if actual_end - actual_start < 3:
        raise RelayError("Too little recorded video for this candidate")
    for a, b in pairwise(selected):
        if b["start"] - a["end"] > 0.25:
            raise RelayError("Recording gap crosses this candidate; skipping incomplete footage")
    output = inside(output, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Generated segment paths are controlled by the recorder; do not accept concat directives.
    listing = output.with_suffix(".concat.txt")
    paths = [inside(s["path"], root) for s in selected]
    if any("'" in str(p) or "\n" in str(p) for p in paths):
        raise RelayError("Unsupported recording path")
    listing.write_text("".join(f"file '{p}'\n" for p in paths))
    try:
        render_atomic(
            [
                binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-n",
                "-protocol_whitelist",
                "file,pipe",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                listing,
                "-ss",
                f"{actual_start - selected[0]['start']:.6f}",
                "-t",
                f"{actual_end - actual_start:.6f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
            ],
            output,
            root,
            expected_duration=actual_end - actual_start,
        )
    finally:
        listing.unlink(missing_ok=True)
    return actual_start, actual_end
