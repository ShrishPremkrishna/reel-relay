"""Shared identity and transport checks for explicitly installed local models.

A loopback HTTP URL alone does not establish local inference: Ollama can proxy
cloud models. Validate its stored model metadata before sending user artifacts.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .util import RelayError, http_json


def local_endpoint(value):
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RelayError("Local inference connects only to a loopback HTTP endpoint")
    return value.rstrip("/")


def model_key(name):
    name = name.removeprefix("registry.ollama.ai/").removeprefix("library/")
    return name if ":" in name.rsplit("/", 1)[-1] else name + ":latest"


def require_model(url, name, *, required_capabilities=()):
    url = local_endpoint(url)
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9._:/-]{1,200}", name):
        raise RelayError("Choose an installed local Ollama model")
    if re.search(r"(?:^|[:-])cloud(?:$|[-:])", name, re.IGNORECASE):
        raise RelayError("Local inference cannot use a cloud-backed model")
    inventory = http_json(url + "/api/tags", timeout=30, local_only=True)
    rows = inventory.get("models") if isinstance(inventory, dict) else None
    if not isinstance(rows, list) or len(rows) > 10000:
        raise RelayError("Ollama returned an invalid local model inventory")
    entry = next(
        (
            row
            for row in rows
            if isinstance(row, dict)
            and isinstance(row.get("name"), str)
            and model_key(row["name"]) == model_key(name)
        ),
        None,
    )
    if not entry:
        raise RelayError("The requested model is not installed locally; install it explicitly first")
    details = http_json(url + "/api/show", method="POST", data={"model": name}, timeout=30, local_only=True)
    if not isinstance(details, dict):
        raise RelayError("Ollama returned invalid local model metadata")
    for record in (entry, entry.get("details", {}), details, details.get("details", {})):
        if not isinstance(record, dict) or record.get("remote_host") or record.get("remote_model"):
            raise RelayError("Local inference cannot use remote model metadata")
    model_details = details.get("details", {})
    size = entry.get("size")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
        or not re.fullmatch(r"[a-fA-F0-9]{64}", str(entry.get("digest", "")))
        or model_details.get("format") not in {"gguf", "safetensors"}
        or not isinstance(details.get("model_info"), dict)
        or not details["model_info"]
    ):
        raise RelayError("Local inference requires a stored model with verifiable local metadata")
    capabilities = details.get("capabilities", [])
    if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
        raise RelayError("Local model capability metadata is invalid")
    missing = set(required_capabilities) - set(capabilities)
    if missing:
        raise RelayError("The chosen local model lacks required capabilities: " + ", ".join(sorted(missing)))
    return {
        "name": entry["name"],
        "digest": entry["digest"],
        "bytes": size,
        "format": model_details["format"],
        "capabilities": details["capabilities"],
    }


def chat_content(response, expected_model, *, max_bytes=65536):
    """Return bounded content only after complete, matching-model generation."""
    if not isinstance(expected_model, str) or not expected_model:
        raise RelayError("A verified local model identity is required")
    if (
        not isinstance(response, dict)
        or response.get("done") is not True
        or response.get("done_reason") != "stop"
    ):
        raise RelayError("Local model generation did not finish completely")
    actual = response.get("model")
    if not isinstance(actual, str) or model_key(actual) != model_key(expected_model):
        raise RelayError("Local response names a different model than the verified input")
    message = response.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > max_bytes:
        raise RelayError("Local model returned empty or oversized content")
    return content
