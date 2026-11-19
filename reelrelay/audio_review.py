"""Deterministic rendered-speech checks for the throughput workflow.

This measures agreement with captions and checks existing technical QA. It does
not assess humor, certify intelligibility to a person, or invent missing speech.
"""

from __future__ import annotations

import math
import re
import time
import unicodedata
import uuid
from array import array
from pathlib import Path

from . import media
from .engine import BuiltinStudio
from .engine.captions import caption_cues, corrected_words, finite, normalize_transcript, timeline_words
from .perception import validate_assessment
from .util import RelayError, inside, read_json, sha256, write_json

MINIMUM_COVERAGE = 0.85
MAXIMUM_WORDS = 2000
SMALL_NUMBERS = dict(
    zip(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ],
        range(20),
        strict=True,
    )
)
TENS = dict(
    zip(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"],
        range(20, 100, 10),
        strict=True,
    )
)


def normalized_words(text):
    """Case/punctuation/Unicode normalization and explicit English 0–99 forms.

    Examples: 45s and forty-fives both become 45s. No fuzzy spelling, synonyms,
    inferred contractions, or transcript repairs are used.
    """
    tokens = re.findall(
        r"[^\W_]+(?:'[^\W_]+)*", unicodedata.normalize("NFKC", text).casefold().replace("’", "'")
    )
    result, index = [], 0
    while index < len(tokens):
        token = tokens[index].replace("'", "")
        plural = token.endswith("s") and token[:-1] in SMALL_NUMBERS
        if token in TENS:
            value, suffix = TENS[token], ""
            if index + 1 < len(tokens):
                following = tokens[index + 1]
                unit = following.removesuffix("s")
                if unit in SMALL_NUMBERS and 0 < SMALL_NUMBERS[unit] < 10:
                    value += SMALL_NUMBERS[unit]
                    suffix = "s" if following.endswith("s") else ""
                    index += 1
            result.append(str(value) + suffix)
        elif token in SMALL_NUMBERS or plural:
            result.append(str(SMALL_NUMBERS[token[:-1] if plural else token]) + ("s" if plural else ""))
        else:
            result.append(token)
        index += 1
    return result


def ordered_alignment(expected, recovered):
    """Longest common subsequence with exact tokens and deterministic ties."""
    if max(len(expected), len(recovered)) > MAXIMUM_WORDS:
        raise RelayError("Rendered speech exceeds the bounded alignment word count")
    rows = [array("H", [0]) * (len(recovered) + 1)]
    for word in expected:
        previous, current = rows[-1], array("H", [0])
        for index, other in enumerate(recovered):
            current.append(previous[index] + 1 if word == other else max(previous[index + 1], current[-1]))
        rows.append(current)
    pairs, left, right = [], len(expected), len(recovered)
    while left and right:
        if expected[left - 1] == recovered[right - 1]:
            pairs.append([left - 1, right - 1])
            left, right = left - 1, right - 1
        elif rows[left - 1][right] >= rows[left][right - 1]:
            left -= 1
        else:
            right -= 1
    pairs.reverse()
    return pairs


def _qa_issues(qa, duration):
    if not isinstance(qa, dict) or not isinstance(qa.get("warnings"), list):
        raise RelayError("Export technical QA is missing or invalid")
    if not all(isinstance(warning, str) and len(warning) <= 1000 for warning in qa["warnings"]):
        raise RelayError("Export technical QA warnings are invalid")
    if not isinstance(qa.get("audio_silent"), bool) or not isinstance(qa.get("loudness"), dict):
        raise RelayError("Export technical QA is missing its audio measurements")
    issues = []
    if qa.get("full_decode") != "passed":
        issues.append("Rendered export did not pass its full media decode check.")
    issues.extend(qa["warnings"][:6])
    if qa["audio_silent"]:
        issues.append("Rendered export has a silent audio track.")
    measurements = {}
    for field in ("input_i", "input_tp"):
        try:
            value = float(qa["loudness"][field])
        except (ValueError, TypeError, KeyError) as exc:
            raise RelayError("Export technical QA has invalid loudness measurements") from exc
        measurements[field] = value if math.isfinite(value) else None
        if not math.isfinite(value):
            issues.append("Rendered audio has a nonfinite loudness or peak measurement.")
    if measurements["input_tp"] is not None and measurements["input_tp"] > -1:
        issues.append("Rendered audio true peak exceeds -1 dBTP.")
    expected = finite(qa.get("expected_duration"), 0.001, 7200, "QA duration")
    if abs(expected - duration) > 0.25:
        issues.append("Technical QA duration does not match this export.")
    return list(dict.fromkeys(issues)), measurements


def _snapshot(export, folder, names, root, paths, snapshots):
    for name in names:
        path = inside(export / name, root)
        if not path.is_file() or path.stat().st_size > 2 * 1024**2:
            raise RelayError("Rendered audio check requires bounded export evidence")
        (folder / name).write_bytes(path.read_bytes())
        paths[name], snapshots[name] = path, sha256(folder / name)


def _unchanged(source, expected_hash, paths, snapshots):
    if sha256(source) != expected_hash or any(
        sha256(path) != snapshots[name] for name, path in paths.items()
    ):
        raise RelayError("Export or its captions/QA changed during the rendered speech check")


def _captionless_technical(
    root, source, expected_hash, folder, manifest, captions, paths, snapshots, duration, started
):
    """Prove the renderer expected no captions, then report technical checks only."""
    _snapshot(source.parent, folder, ("edit.json", "transcript.json"), root, paths, snapshots)
    if (
        manifest.get("timeline_sha256") != snapshots["edit.json"]
        or manifest.get("transcript_sha256") != snapshots["transcript.json"]
        or any(
            manifest["artifact_sha256"].get(name) != snapshots[name]
            for name in ("edit.json", "transcript.json")
        )
        or not re.fullmatch(r"[a-fA-F0-9]{64}", str(manifest.get("source_sha256", "")))
    ):
        raise RelayError("Caption-free export requires manifest-bound edit and transcript evidence")
    edit = read_json(folder / "edit.json")
    if not isinstance(edit, dict) or edit.get("verdict") != "accept":
        raise RelayError("Caption-free technical review requires an accepted edit")
    keep = edit.get("keep")
    if not isinstance(keep, list) or not 1 <= len(keep) <= 100:
        raise RelayError("Caption-free edit must contain bounded retained ranges")
    cuts = []
    for cut in keep:
        if not isinstance(cut, dict):
            raise RelayError("Caption-free edit has an invalid retained range")
        start = finite(cut.get("start"), 0, 7200, "Retained source start")
        end = finite(cut.get("end"), start + 0.001, 7200, "Retained source end")
        cuts.append({"start": start, "end": end})
    if abs(sum(cut["end"] - cut["start"] for cut in cuts) - duration) > 0.05:
        raise RelayError("Caption-free edit duration does not match the export")
    transcript = normalize_transcript(read_json(folder / "transcript.json"), 7200, manifest["source_sha256"])
    corrections = edit.get("caption_corrections", [])
    if captions.get("corrections", []) != corrections:
        raise RelayError("Caption-free export corrections do not match its edit")
    retained = timeline_words(corrected_words(transcript, corrections, 7200), cuts)
    if caption_cues(retained):
        raise RelayError("Empty rendered captions conflict with speech cues expected by the retained edit")
    plan = {
        "source_transcript_word_count": len(transcript["words"]),
        "retained_transcript_word_count": len(retained),
        "expected_caption_cue_count": 0,
        "keep": cuts,
        "basis": "Recomputed the renderer's caption timeline from its frozen edit and transcript; no retained cues are expected. This does not establish absence of audible speech.",
    }
    write_json(folder / "caption-plan.json", plan)
    qa = read_json(folder / "qa.json")
    issues, loudness = _qa_issues(qa, duration)
    checks = {
        "manifest_integrity": True,
        "caption_plan_empty": True,
        "edit_duration": True,
        "media_duration": True,
        "audio_stream": True,
        "full_decode": qa.get("full_decode") == "passed",
        "qa_duration": abs(qa["expected_duration"] - duration) <= 0.25,
        "qa_warnings_absent": qa["warnings"] == [],
        "audio_non_silent": not qa["audio_silent"],
        "loudness_finite": all(value is not None for value in loudness.values()),
        "true_peak_within_limit": loudness["input_tp"] is not None and loudness["input_tp"] <= -1,
    }
    passed = all(checks.values()) and not issues
    summary = (
        "Technical audio checks "
        + ("passed" if passed else "found defects")
        + ". The accepted edit expects no speech caption cues. No ASR or listening review was performed; "
        "speech content, intelligibility and the visual payoff are not assessed by this receipt."
    )
    assessment = validate_assessment(
        {
            "summary": summary,
            "events": [],
            "speech": [],
            "audio_issues": issues,
            "visual_issues": [],
            "confidence": 0.0,
        },
        duration,
    )
    _unchanged(source, expected_hash, paths, snapshots)
    packet = {
        "schema_version": 1,
        "adapter_version": 1,
        "provider": "local-technical",
        "model": "ffmpeg",
        "check_type": "technical-audio",
        "purpose": "preview",
        "modalities": ["audio"],
        "source_sha256": expected_hash,
        "created_at": time.time(),
        "duration": duration,
        "complete": True,
        "passed": passed,
        "speech_assessed": False,
        "checks": checks,
        "assessment": assessment,
        "metrics": {
            "expected_word_count": 0,
            "recovered_word_count": None,
            "matched_word_count": None,
            "caption_word_coverage": None,
        },
        "loudness": loudness,
        "timing_quality": "Export duration checked; no speech or caption synchronization assessment",
        "speech_verification": "Not assessed; the frozen edit expects no speech caption cues",
        "confidence_basis": "Speech confidence is not assessed (0); technical pass/fail is recorded separately in checks",
        "limitations": [
            "Empty source transcript cues do not prove that no speech or singing is audible.",
            "No speech content, intelligibility, humor, vocal emotion or visual payoff was assessed.",
            "Decode and loudness results come from the completed renderer QA bound to these exact export bytes; duration/audio-stream presence was freshly probed.",
        ],
        "inputs": [
            {
                "export_manifest_sha256": snapshots["manifest.json"],
                "artifact_sha256": {path.name: sha256(path) for path in folder.iterdir() if path.is_file()},
                "qa_provenance": "Manifest-bound full decode and loudness measurements of the exact export",
            }
        ],
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    write_json(folder / "report.json", packet)
    return folder / "report.json"


def inspect_rendered_speech(cfg, candidate):
    """Check captions when expected, otherwise verify technical audio evidence."""
    root = Path(cfg["data_dir"]).resolve()
    source = Path(candidate["output_path"])
    source = inside(source if source.is_absolute() else root / source, root)
    expected_hash = candidate["output_hash"]
    folder = root / "perception" / uuid.uuid4().hex
    folder.mkdir(parents=True)
    provider, model = "local-asr", None
    started = time.monotonic()
    try:
        if sha256(source) != expected_hash:
            raise RelayError("Rendered export changed before the speech check")
        export = source.parent
        paths, snapshots = {}, {}
        _snapshot(export, folder, ("manifest.json", "captions.json", "qa.json"), root, paths, snapshots)
        manifest = read_json(folder / "manifest.json")
        if (
            manifest.get("complete") is not True
            or manifest.get("output_sha256") != expected_hash
            or not isinstance(manifest.get("artifact_sha256"), dict)
            or any(
                manifest["artifact_sha256"].get(name) != snapshots[name]
                for name in ("captions.json", "qa.json")
            )
        ):
            raise RelayError("Export captions or technical QA do not match the completed manifest")
        duration = finite(manifest.get("duration"), 0.001, 180, "Speech review duration")
        info = media.probe(source, root)
        if abs(info["duration"] - duration) > 0.25 or not any(
            row.get("codec_type") == "audio" for row in info["streams"]
        ):
            raise RelayError("Rendered export duration or audio stream does not match its manifest")
        captions = read_json(folder / "captions.json")
        cues = captions.get("cues") if isinstance(captions, dict) else None
        if not isinstance(cues, list) or len(cues) > MAXIMUM_WORDS:
            raise RelayError("Rendered speech check requires a bounded list of timed caption cues")
        if not cues:
            provider, model = "local-technical", "ffmpeg"
            return _captionless_technical(
                root, source, expected_hash, folder, manifest, captions, paths, snapshots, duration, started
            )
        engine = BuiltinStudio(cfg)
        model = engine.model_path().name
        caption_text, previous = [], -1.0
        for cue in cues:
            if not isinstance(cue, dict) or not isinstance(cue.get("text"), str) or len(cue["text"]) > 500:
                raise RelayError("Rendered caption cue is invalid")
            start = finite(cue.get("start"), 0, duration, "Caption start")
            end = finite(cue.get("end"), start, duration + 0.1, "Caption end")
            if start < previous or end <= start:
                raise RelayError("Rendered caption cues must have positive chronological ranges")
            previous = start
            caption_text.append(cue["text"])
        expected = normalized_words(" ".join(caption_text))
        if not expected or len(expected) > MAXIMUM_WORDS:
            raise RelayError("Rendered captions have no bounded recognizable word sequence")
        issues, loudness = _qa_issues(read_json(folder / "qa.json"), duration)
        wav = folder / "rendered-audio.wav"
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
                source,
                "-vn",
                "-af",
                f"aresample=16000:async=1:first_pts=0,apad,atrim=duration={duration}",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                wav,
            ],
            root,
        )
        transcript, provenance = engine.transcribe_audio(wav, folder, duration, expected_hash)
        write_json(folder / "transcript.json", transcript)
        recovered = normalized_words(" ".join(row["text"] for row in transcript["words"]))
        pairs = ordered_alignment(expected, recovered)
        coverage = len(pairs) / len(expected)
        if len(expected) < 3:
            issues.append(
                "Fewer than three caption words; rendered speech agreement is insufficient to assess."
            )
        if coverage < MINIMUM_COVERAGE:
            issues.append(
                f"Rendered speech matches {len(pairs)}/{len(expected)} caption words in order ({coverage:.1%}); at least 85% is required."
            )
        agreement = {
            "method": "exact normalized-token longest common subsequence",
            "normalization": "Unicode NFKC, case/punctuation, explicit English numbers 0–99 and plurals; no fuzzy repairs",
            "expected_words": expected,
            "recovered_words": recovered,
            "matched_indices": pairs,
            "expected_word_count": len(expected),
            "recovered_word_count": len(recovered),
            "matched_word_count": len(pairs),
            "caption_word_coverage": coverage,
            "required_coverage": MINIMUM_COVERAGE,
            "minimum_expected_words": 3,
        }
        write_json(folder / "alignment.json", agreement)
        summary = f"Offline ASR recovered {len(recovered)} words; {len(pairs)}/{len(expected)} caption words matched in order ({coverage:.1%}). Confidence is measured word agreement, not a listening judgment."
        assessment = validate_assessment(
            {
                "summary": summary,
                "events": [
                    {"start": 0.0, "end": duration, "visual": "", "audio": summary, "confidence": coverage}
                ],
                "speech": [
                    {"start": row["start"], "end": row["end"], "text": row["text"], "confidence": coverage}
                    for row in transcript["segments"][:100]
                    if row["end"] > row["start"]
                ],
                "audio_issues": list(dict.fromkeys(issues))[:12],
                "visual_issues": [],
                "confidence": coverage,
            },
            duration,
        )
        _unchanged(source, expected_hash, paths, snapshots)
        packet = {
            "schema_version": 1,
            "adapter_version": 1,
            "provider": "local-asr",
            "model": model,
            "purpose": "preview",
            "modalities": ["audio"],
            "source_sha256": expected_hash,
            "created_at": time.time(),
            "duration": duration,
            "complete": True,
            "passed": not assessment["audio_issues"],
            "assessment": assessment,
            "metrics": {
                key: value
                for key, value in agreement.items()
                if key.endswith("count")
                or key in {"caption_word_coverage", "required_coverage", "minimum_expected_words"}
            },
            "loudness": loudness,
            "timing_quality": "Rendered-audio ASR timing; word-order agreement does not certify caption synchronization",
            "speech_verification": "Offline machine recognition compared with captions; neither is guaranteed ground truth",
            "confidence_basis": "Measured ordered caption-word coverage, not model certainty or human listening",
            "limitations": [
                "Does not assess humor, vocal emotion, comic timing, or whether a person can understand every word.",
                "ASR and captions can share recognition errors; repeated words can match incidentally.",
                "Word-order agreement does not certify exact caption timing or absence of unwanted extra sounds.",
            ],
            "inputs": [
                {
                    "audio_path": str(wav),
                    "audio_sha256": sha256(wav),
                    "asr": provenance,
                    "artifact_sha256": {
                        path.name: sha256(path) for path in folder.iterdir() if path.is_file()
                    },
                    "export_manifest_sha256": snapshots["manifest.json"],
                }
            ],
            "wall_seconds": round(time.monotonic() - started, 3),
        }
        write_json(folder / "report.json", packet)
        return folder / "report.json"
    except Exception as exc:
        write_json(
            folder / "failure.json",
            {
                "source_sha256": expected_hash,
                "purpose": "preview",
                "provider": provider,
                "model": model,
                "complete": False,
                "type": type(exc).__name__,
                "message": str(exc)[:1000]
                if isinstance(exc, RelayError)
                else "Rendered speech check did not complete",
            },
        )
        raise
