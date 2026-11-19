"""Small prompt views of evidence; original packets remain the validation record."""

from __future__ import annotations

import copy
import re
from pathlib import PurePath

_PRIVATE_FIELDS = {
    "apikey",
    "xapikey",
    "xgoogapikey",
    "authorization",
    "accesstoken",
    "refreshtoken",
    "clientsecret",
    "password",
    "credentials",
    "secret",
    "token",
    "secretaccesskey",
    "accesskeyid",
    "rawresponse",
    "rawresponses",
    "apiresponse",
    "httpresponse",
    "response",
}
_PACKET_PATHS = {"artifact_root", "source_path", "audio_path", "video_path", "project_manifest"}
_ASSET_FIELDS = {"path", "kind", "title", "tags", "duration", "rights"}
_RECEIPT_BOOKKEEPING = {
    "audio_path",
    "video_path",
    "image_paths",
    "input_paths",
    "path",
    "paths",
    "audio_sha256",
    "video_sha256",
    "image_sha256",
    "observation_sha256",
    "response_sha256",
    "model_digest",
    "model_bytes",
    "bytes",
    "created_at",
    "wall_seconds",
    "eval_count",
    "eval_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "load_duration",
    "total_duration",
    "response_model",
    "usage",
    "usage_metadata",
    "call_stats",
}


def _private_field(key):
    name = re.sub(r"[^a-z0-9]", "", str(key).casefold())
    return name in _PRIVATE_FIELDS or name.endswith(
        (
            "apikey",
            "accesstoken",
            "refreshtoken",
            "secretaccesskey",
            "accesskeyid",
            "rawresponse",
            "rawresponses",
        )
    )


def _copy_evidence(value):
    """Remove structured credential/raw-transport fields, preserving all other data."""
    if isinstance(value, dict):
        return {key: _copy_evidence(item) for key, item in value.items() if not _private_field(key)}
    if isinstance(value, list):
        return [_copy_evidence(item) for item in value]
    return copy.deepcopy(value)


def _joined_text(rows):
    return " ".join(" ".join(str(row.get("text", "")) for row in rows).split())


def _transcript(value):
    if not isinstance(value, dict):
        return value
    words = value.get("words")
    if not (
        isinstance(words, list)
        and words
        and all(isinstance(word, dict) and {"start", "end", "text"} <= word.keys() for word in words)
    ):
        # Segment-only and already compact transcripts keep their available evidence.
        return value
    columns = ["start", "end", "text", "probability"]
    for word in words:
        columns.extend(key for key in word if key not in columns)
    text = _joined_text(words)
    segments = value.get("segments")
    if (
        isinstance(segments, list)
        and all(isinstance(segment, dict) for segment in segments)
        and _joined_text(segments) == text
    ):
        # Only duplicated text is removed. Confidence/uncertainty annotations and
        # their segment boundaries survive, even when word text is authoritative.
        metadata = [
            {key: item for key, item in segment.items() if key != "text"}
            for segment in segments
            if set(segment) - {"start", "end", "text"}
        ]
        del value["segments"]
        if metadata:
            value["segments"] = metadata
    if isinstance(value.get("text"), str) and " ".join(value["text"].split()) == text:
        del value["text"]
    value["word_columns"] = columns
    # Null means the input supplied no value, never an inferred confidence score.
    value["words"] = [[word.get(column) for column in columns] for word in words]
    return value


def _perception(value):
    if not isinstance(value, dict):
        return value
    result = {key: item for key, item in value.items() if key not in _RECEIPT_BOOKKEEPING}
    for field in ("inputs", "raw_observations"):
        if not isinstance(result.get(field), list):
            continue
        windows = []
        for window in result[field]:
            if not isinstance(window, dict):
                windows.append(window)
                continue
            compact = {key: item for key, item in window.items() if key not in _RECEIPT_BOOKKEEPING}
            if compact.get("model") == result.get("model"):
                compact.pop("model", None)
            # Keep the entire observation, including uncertainties omitted from
            # aggregate assessments, and measured silence/clipping signal data.
            windows.append(compact)
        result[field] = windows
    return result


def compact_packet(packet: dict) -> dict:
    """Return a detached JSON prompt view without changing or reading any files.

    Images remain separately attached by the caller; their ordered basenames are
    retained as image_names. Timed words use word_columns plus row arrays, with
    exact supplied numbers/text and null for missing values. Original evidence
    must still be used for validation, receipt verification and asset credits.
    """
    if not isinstance(packet, dict):
        raise TypeError("Editorial evidence must be a dictionary")
    result = _copy_evidence(packet)
    for key in _PACKET_PATHS:
        result.pop(key, None)
    images = result.pop("images", None)
    if isinstance(images, list) and "image_names" not in result:
        result["image_names"] = [PurePath(path).name for path in images]
    if isinstance(result.get("assets"), list):
        # Keep rights verbatim: truncation could conceal a usage restriction.
        result["assets"] = [
            {key: item for key, item in asset.items() if key in _ASSET_FIELDS}
            if isinstance(asset, dict)
            else asset
            for asset in result["assets"]
        ]
    if "transcript" in result:
        result["transcript"] = _transcript(result["transcript"])
    if "perception" in result:
        result["perception"] = _perception(result["perception"])
    return result
