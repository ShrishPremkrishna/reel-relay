"""Deterministic FFmpeg timeline rendering, independent of the legacy studio."""

from __future__ import annotations

import json
import math
import subprocess

from .. import media
from ..util import RelayError, inside
from .captions import finite


def filter_path(path):
    # There are two parsers: the option value, then the complete filter graph.
    # argv bypasses the shell, so shell quoting would be both wrong and unsafe.
    option = "".join("\\" + char if char in "\\':" else char for char in str(path))
    return "".join("\\" + char if char in "\\'[],;" else char for char in option)


def region(value):
    if not isinstance(value, dict):
        raise RelayError("A framing region must be a rectangle")
    result = {
        key: finite(value.get(key), 0.001 if key in ("width", "height") else 0, 1, "Framing " + key)
        for key in ("x", "y", "width", "height")
    }
    if result["x"] + result["width"] > 1.000001 or result["y"] + result["height"] > 1.000001:
        raise RelayError("Framing region extends beyond the source")
    return result


def crop_region(rect, source_width, source_height):
    rect = region(rect)
    w = max(2, int(rect["width"] * source_width) // 2 * 2)
    h = max(2, int(rect["height"] * source_height) // 2 * 2)
    x = min(source_width - w, int(rect["x"] * source_width) // 2 * 2)
    y = min(source_height - h, int(rect["y"] * source_height) // 2 * 2)
    return f"crop={w}:{h}:{x}:{y}"


def fit_cover(width, height, *, focus_x=0.5, focus_y=0.5, zoom=1.0):
    focus_x = finite(focus_x, 0, 1, "Horizontal focus")
    focus_y = finite(focus_y, 0, 1, "Vertical focus")
    zoom = finite(zoom, 1, 1.5, "Zoom")
    return f"scale={math.ceil(width * zoom / 2) * 2}:{math.ceil(height * zoom / 2) * 2}:force_original_aspect_ratio=increase,crop={width}:{height}:(iw-ow)*{focus_x}:(ih-oh)*{focus_y},setsar=1"


def layout_filters(label, target, decision, cut, info, width, height, tag):
    framing = decision.get("framing")
    mode = decision.get("layout", "contain")
    if framing:
        if framing.get("mode") != "panels" or len(framing.get("regions", [])) != 2:
            raise RelayError("Custom framing needs exactly two inspected panels")
        ratio = finite(framing.get("split", 0.48), 0.25, 0.75, "Panel split")
        top_height = int(height * ratio) // 2 * 2
        bottom_height = height - top_height
        top, bottom = framing["regions"]
        return [
            f"[{label}]split=2[{tag}a][{tag}b]",
            f"[{tag}a]{crop_region(top, info['width'], info['height'])},{fit_cover(width, top_height)}[{tag}top]",
            f"[{tag}b]{crop_region(bottom, info['width'], info['height'])},{fit_cover(width, bottom_height)}[{tag}bottom]",
            f"[{tag}top][{tag}bottom]vstack=inputs=2[{target}]",
        ]
    if mode == "focus":
        return [
            f"[{label}]{fit_cover(width, height, focus_x=cut.get('focus_x', 0.5), focus_y=cut.get('focus_y', 0.5), zoom=cut.get('zoom', 1))}[{target}]"
        ]
    if mode == "split":
        top_height = int(height * 0.30) // 2 * 2
        bottom_height = height - top_height
        # A large reaction panel + the full, uncropped game with a blurred fill.
        return [
            f"[{label}]split=3[{tag}face][{tag}bg][{tag}game]",
            f"[{tag}face]{crop_region(decision.get('facecam'), info['width'], info['height'])},{fit_cover(width, top_height)}[{tag}top]",
            f"[{tag}bg]{fit_cover(width, bottom_height)},boxblur=20:2[{tag}back]",
            f"[{tag}game]scale={width}:{bottom_height}:force_original_aspect_ratio=decrease,setsar=1[{tag}front]",
            f"[{tag}back][{tag}front]overlay=(W-w)/2:(H-h)/2[{tag}bottom]",
            f"[{tag}top][{tag}bottom]vstack=inputs=2[{target}]",
        ]
    if mode != "contain":
        raise RelayError("Unsupported framing mode")
    return [
        f"[{label}]split=2[{tag}bg][{tag}fg]",
        f"[{tag}bg]{fit_cover(width, height)},boxblur=20:2[{tag}back]",
        f"[{tag}fg]scale={width}:{height}:force_original_aspect_ratio=decrease,setsar=1[{tag}front]",
        f"[{tag}back][{tag}front]overlay=(W-w)/2:(H-h)/2[{target}]",
    ]


def encode(source, info, decision, assets, subtitles, output, root, *, width=1080, height=1920, fps=30):
    cuts = decision.get("keep", [])
    if not 1 <= len(cuts) <= 12:
        raise RelayError("An edit must contain 1–12 source ranges")
    duration = 0.0
    for cut in cuts:
        start = finite(cut.get("start"), 0, info["duration"], "Cut start")
        end = finite(cut.get("end"), start + 0.05, info["duration"], "Cut end")
        duration += end - start
    if not 5 <= duration <= 90:
        raise RelayError("An export must be 5–90 seconds long")
    args = [
        media.binary("ffmpeg"),
        "-v",
        "error",
        "-nostdin",
        "-n",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        source,
    ]
    graph, pairs = [], []
    for index, cut in enumerate(cuts):
        start, end = cut["start"], cut["end"]
        graph.append(f"[0:v]trim=start={start}:end={end},setpts=PTS-STARTPTS,fps={fps}[trim{index}]")
        graph.extend(
            layout_filters(f"trim{index}", f"v{index}", decision, cut, info, width, height, f"layout{index}")
        )
        if info["has_audio"]:
            graph.append(
                f"[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{index}]"
            )
        else:
            graph.append(
                f"anullsrc=r=48000:cl=stereo,atrim=duration={end - start},asetpts=PTS-STARTPTS[a{index}]"
            )
        pairs.append(f"[v{index}][a{index}]")
    graph.append("".join(pairs) + f"concat=n={len(cuts)}:v=1:a=1[mainv][maina]")
    graph.append("[maina]loudnorm=I=-16:TP=-2:LRA=11,aresample=48000[voice]")
    sound_labels, video_label = ["[voice]"], "mainv"
    effects = [("sfx", item) for item in decision.get("sfx", [])] + [
        ("overlay", item) for item in decision.get("overlays", [])
    ]
    for index, (kind, effect) in enumerate(effects, 1):
        asset = assets.get(effect["asset_path"])
        if not asset:
            raise RelayError("Effect is not in the configured asset catalog")
        path = inside(asset["resolved_path"], root)
        at = finite(effect.get("at"), 0, duration, "Effect time")
        length = finite(effect.get("duration"), 0.05, duration - at, "Effect duration")
        source_start = finite(
            effect.get("source_start", 0), 0, max(0, asset["duration"] - length) + 0.1, "Asset offset"
        )
        if source_start + length > asset["duration"] + 0.05:
            raise RelayError("Effect interval exceeds its decoded asset duration")
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            args.extend(["-loop", "1", "-framerate", str(fps)])
        args.extend(["-protocol_whitelist", "file,pipe", "-i", path])
        if kind == "sfx":
            gain = finite(effect.get("gain_db"), -36, -6, "Sound gain")
            if asset["kind"] != "sfx" or not asset.get("has_audio"):
                raise RelayError("Sound asset has no audio")
            fade = min(0.04, length / 4)
            graph.append(
                f"[{index}:a]atrim=start={source_start}:duration={length},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={gain}dB,afade=t=in:d={fade},afade=t=out:st={length - fade}:d={fade},adelay={round(at * 1000)}:all=1[sound{index}]"
            )
            sound_labels.append(f"[sound{index}]")
        else:
            if asset["kind"] not in {"meme", "broll"} or not asset.get("has_video"):
                raise RelayError("Reaction asset has no visual stream")
            ow = round(width * finite(effect.get("width_fraction"), 0.15, 1, "Overlay width") / 2) * 2
            oh = round(height * finite(effect.get("height_fraction"), 0.15, 1, "Overlay height") / 2) * 2
            x = finite(effect.get("x"), 0, 1, "Overlay x")
            y = finite(effect.get("y"), 0, 1, "Overlay y")
            graph.append(
                f"[{index}:v]trim=start={source_start}:duration={length},setpts=PTS-STARTPTS+{at}/TB,fps={fps},scale={ow}:{oh}:force_original_aspect_ratio=decrease,format=rgba[overlay{index}]"
            )
            graph.append(
                f"[{video_label}][overlay{index}]overlay=x=(W-w)*{x}:y=(H-h)*{y}:enable='between(t,{at},{at + length})':eof_action=pass:repeatlast=0[composite{index}]"
            )
            video_label = f"composite{index}"
    graph.append(
        "".join(sound_labels)
        + f"amix=inputs={len(sound_labels)}:duration=first:normalize=0:dropout_transition=0,alimiter=limit=0.84:level=false,aresample=48000[aout]"
    )
    graph.append(f"[{video_label}]ass=filename={filter_path(subtitles)},format=yuv420p[vout]")
    args.extend(
        [
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-r",
            str(fps),
            "-t",
            str(duration),
            "-movflags",
            "+faststart",
        ]
    )
    return media.render_atomic(args, output, root, timeout=1200, expected_duration=duration)


def technical_qa(path, root, expected_duration):
    media.run(
        [
            media.binary("ffmpeg"),
            "-v",
            "error",
            "-xerror",
            "-nostdin",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            path,
            "-f",
            "null",
            "-",
        ],
        root,
        timeout=300,
    )
    args = [
        media.binary("ffmpeg"),
        "-v",
        "info",
        "-nostdin",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        path,
        "-vn",
        "-af",
        "loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(
        media.command(args, root),
        cwd=root,
        env=media.environment(root),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode:
        raise RelayError("The encoded audio could not be measured")
    start, end = result.stderr.rfind("{"), result.stderr.rfind("}")
    if start < 0 or end <= start:
        raise RelayError("The audio meter did not return a report")
    measured = json.loads(result.stderr[start : end + 1])
    warnings = []
    peak = float(measured["input_tp"])
    if math.isfinite(peak) and peak > -1.0:
        warnings.append("True peak exceeds -1 dBTP")
    return {
        "full_decode": "passed",
        "expected_duration": round(expected_duration, 3),
        "loudness": measured,
        "warnings": warnings,
        "audio_silent": measured.get("input_i") == "-inf",
        "human_review": [
            "speech intelligibility",
            "caption accuracy",
            "framing and platform UI margins",
            "hook/payoff and comic timing",
        ],
    }
