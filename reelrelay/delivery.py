"""Time-limited HTTPS delivery of reviewed exports to Instagram's fetcher."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlencode, urlparse

from .credentials import Vault
from .store import Store
from .util import RelayError, inside, sha256


def signing_key(cfg):
    vault = Vault(cfg)
    record = vault.get("delivery-signing")
    if not record:
        with Store(cfg).lock("delivery-key-init", seconds=30):
            record = vault.get("delivery-signing")
            if not record:
                record = {"key": secrets.token_hex(32)}
                vault.put("delivery-signing", record)
    return bytes.fromhex(record["key"])


def signature(cfg, cid, output_hash, expires):
    body = f"{cid}:{output_hash}:{expires}".encode()
    return hmac.new(signing_key(cfg), body, hashlib.sha256).hexdigest()


def delivery_url(cfg, candidate, *, ttl=86400):
    base = cfg["web"]["public_base_url"].rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RelayError("Publishing needs your server's public HTTPS address or an S3 media host")
    if candidate["is_test"] or not (candidate.get("review") or {}).get("approved"):
        raise RelayError("Only reviewed, non-test exports can be delivered publicly")
    expires = int(time.time() + ttl)
    query = urlencode(
        {"expires": expires, "signature": signature(cfg, candidate["id"], candidate["output_hash"], expires)}
    )
    return f"{base}/delivery/{candidate['id']}.mp4?{query}"


def resolve_delivery(cfg, cid, expires, supplied_signature):
    now = time.time()
    if not now < expires <= now + 86400 + 60:
        raise RelayError("Media delivery link expired")
    candidate = Store(cfg).get(cid)
    if (
        candidate["is_test"]
        or not candidate["output_hash"]
        or not (candidate.get("review") or {}).get("approved")
    ):
        raise RelayError("Export is unavailable for public delivery")
    expected = signature(cfg, cid, candidate["output_hash"], expires)
    if not hmac.compare_digest(expected, supplied_signature):
        raise RelayError("Invalid media delivery signature")
    path = inside(candidate["output_path"], cfg["data_dir"])
    if not path.is_file() or sha256(path) != candidate["output_hash"]:
        raise RelayError("Reviewed export changed or is missing")
    return path
