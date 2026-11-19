"""Validated source-time transcripts and deterministic output-time captions."""

from __future__ import annotations

import math
import re

from ..util import RelayError


def finite(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RelayError(f"{name} must be a finite number")
    if not low <= value <= high:
        raise RelayError(f"{name} is outside the media bounds")
    return float(value)


def normalize_transcript(payload, duration, source_hash):
    if not isinstance(payload, dict) or not isinstance(payload.get("words"), list):
        raise RelayError("Transcript must contain timed words")
    if payload.get("source_sha256") not in (None, source_hash):
        raise RelayError("Transcript belongs to a different source")
    if len(payload["words"]) > 20000:
        raise RelayError("Transcript exceeds the word limit")
    words, previous = [], -1.0
    for item in payload["words"]:
        if not isinstance(item, dict):
            raise RelayError("Invalid transcript word")
        start = finite(item.get("start"), 0, duration + 0.1, "Word start")
        end = finite(item.get("end"), start, duration + 0.1, "Word end")
        text = item.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise RelayError("Transcript words must be short, nonempty text")
        if start < previous:
            raise RelayError("Transcript words must be chronological")
        previous = start
        word = {
            "start": round(min(start, duration), 3),
            "end": round(min(end, duration), 3),
            "text": text.strip(),
        }
        if "probability" in item:
            word["probability"] = finite(item["probability"], 0, 1, "Word probability")
        words.append(word)
    # Preserve descriptive fields only, never arbitrary provider responses or instructions.
    return {
        "schema_version": 1,
        "words": words,
        "segments": [
            {"start": cue["start"], "end": cue["end"], "text": cue["text"]}
            for cue in caption_cues(words, maximum_words=20, maximum_characters=180)
        ],
        "backend": str(payload.get("backend", "imported"))[:80],
        "language": str(payload.get("language", "unknown"))[:32],
        "provenance": str(payload.get("provenance", "No transcription provenance supplied"))[:1000],
        "source_sha256": source_hash,
    }


def corrected_words(transcript, corrections, duration):
    """Caption corrections never alter source audio or the stored ASR transcript."""
    words = [dict(word) for word in transcript["words"]]
    if not isinstance(corrections, list) or len(corrections) > 100:
        raise RelayError("Caption corrections must be a bounded list")
    windows = []
    for item in corrections:
        if not isinstance(item, dict):
            raise RelayError("Invalid caption correction")
        start = finite(item.get("start"), 0, duration, "Correction start")
        end = finite(item.get("end"), start + 0.01, duration, "Correction end")
        text = item.get("text")
        if not isinstance(text, str) or len(text) > 500:
            raise RelayError("Correction text is too long")
        if any(start < old_end and end > old_start for old_start, old_end in windows):
            raise RelayError("Caption correction windows overlap")
        windows.append((start, end))
        words = [word for word in words if word["end"] <= start or word["start"] >= end]
        tokens = text.split()
        for index, token in enumerate(tokens):
            words.append(
                {
                    "start": start + (end - start) * index / len(tokens),
                    "end": start + (end - start) * (index + 1) / len(tokens),
                    "text": token,
                    "corrected": True,
                }
            )
    return sorted(words, key=lambda word: (word["start"], word["end"]))


def timeline_words(words, cuts):
    result, offset = [], 0.0
    for cut in cuts:
        start, end = cut["start"], cut["end"]
        for word in words:
            midpoint = (word["start"] + word["end"]) / 2
            if start <= midpoint < end:
                a, b = max(start, word["start"]), min(end, word["end"])
                if b > a:
                    result.append(
                        {**word, "start": round(offset + a - start, 3), "end": round(offset + b - start, 3)}
                    )
        offset += end - start
    return result


def caption_cues(words, maximum_words=3, maximum_characters=28):
    cues, group = [], []

    def flush():
        if group:
            cues.append(
                {
                    "start": group[0]["start"],
                    "end": group[-1]["end"],
                    "text": " ".join(word["text"] for word in group),
                }
            )
            group.clear()

    for word in words:
        if group and (
            len(group) >= maximum_words
            or word["start"] - group[-1]["end"] > 0.45
            or len(" ".join(w["text"] for w in group)) + len(word["text"]) + 1 > maximum_characters
        ):
            flush()
        group.append(word)
        if re.search(r"[.!?]$", word["text"]):
            flush()
    flush()
    return cues


def timestamp(seconds, *, srt=False):
    scale = 1000 if srt else 100
    ticks = round(max(0, seconds) * scale)
    hours, rem = divmod(ticks, 3600 * scale)
    minutes, rem = divmod(rem, 60 * scale)
    whole, fraction = divmod(rem, scale)
    if srt:
        return f"{hours:02}:{minutes:02}:{whole:02},{fraction:03}"
    return f"{hours}:{minutes:02}:{whole:02}.{fraction:02}"


def ass_text(text):
    # ASS overrides are executable formatting. Render these characters literally.
    return " ".join(text.split()).replace("\\", "＼").replace("{", "｛").replace("}", "｝")


def write_subtitles(folder, cues, hook, duration, *, width=1080, height=1920, render_captions=True):
    scale = width / 1080
    font_size = round(60 * scale)
    margin = round(270 * height / 1920)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Speech,Arial,{font_size},&H00FFFFFF,&H00FFFFFF,&H00141414,&H70000000,-1,0,0,0,100,100,0,0,1,{3.5 * scale},0,2,{round(80 * scale)},{round(140 * scale)},{margin},1
Style: Hook,Arial,{round(54 * scale)},&H00FFFFFF,&H00FFFFFF,&H00141414,&H70000000,-1,0,0,0,100,100,0,0,1,{3.5 * scale},0,8,{round(75 * scale)},{round(100 * scale)},{round(110 * height / 1920)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = [
        f"Dialogue: 0,{timestamp(cue['start'])},{timestamp(cue['end'])},Speech,,0,0,0,,{ass_text(cue['text'])}\n"
        for cue in (cues if render_captions else [])
    ]
    if hook:
        text = ass_text(hook[:80])
        if len(text) > 38:
            split = text.rfind(" ", 0, 42)
            if split > 10:
                text = text[:split] + r"\N" + text[split + 1 :]
        events.append(f"Dialogue: 1,{timestamp(0)},{timestamp(min(duration, 2.5))},Hook,,0,0,0,,{text}\n")
    (folder / "captions.ass").write_text(header + "".join(events))
    (folder / "captions.srt").write_text(
        "\n\n".join(
            f"{index + 1}\n{timestamp(cue['start'], srt=True)} --> {timestamp(cue['end'], srt=True)}\n{cue['text']}"
            for index, cue in enumerate(cues)
        )
        + "\n"
    )
