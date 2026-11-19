from __future__ import annotations

import os
import random
import time
from urllib.parse import urlparse

from .config import load
from .credentials import Vault
from .delivery import delivery_url
from .oauth import instagram_token
from .store import LockBusy, Store
from .util import RelayError, http_json, inside, sha256


class AccountUnavailable(RelayError):
    """Account/session/quota failures must not invalidate approved media."""


class Instagram:
    def __init__(self, cfg, *, request=http_json):
        self.token = instagram_token(cfg, request=request)
        self.base = "https://graph.instagram.com/" + cfg["publish"]["api_version"]
        self.request = request

    def call(self, path, *, method="GET", data=None):
        return self.request(
            self.base + "/" + path,
            method=method,
            data=data,
            headers={"Authorization": "Bearer " + self.token["access_token"]},
        )

    def limit(self):
        return self.call(self.token["account_id"] + "/content_publishing_limit?fields=config,quota_usage")

    def create(self, url, caption):
        return self.call(
            self.token["account_id"] + "/media",
            method="POST",
            data={"media_type": "REELS", "video_url": url, "caption": caption, "share_to_feed": True},
        )["id"]

    def status(self, container_id):
        return self.call(container_id + "?fields=status_code,status")

    def publish(self, container_id):
        return self.call(
            self.token["account_id"] + "/media_publish", method="POST", data={"creation_id": container_id}
        )["id"]

    def permalink(self, media_id):
        return self.call(media_id + "?fields=permalink").get("permalink")


def public_url(cfg, candidate):
    if candidate.get("public_url"):
        url = candidate["public_url"]
    else:
        settings = cfg["publish"]
        if not settings["s3_bucket"] and cfg["web"]["public_base_url"]:
            return delivery_url(cfg, candidate)
        if not settings["s3_bucket"] or not settings["public_media_base"]:
            raise RelayError("Configure a public media host or supply the exported Reel's HTTPS media URL")
        parsed = urlparse(settings["public_media_base"])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise RelayError("Configure a public HTTPS media host before uploading")
        try:
            import boto3
        except ImportError:
            raise RelayError("Install the optional s3 dependencies for your media host") from None
        path = inside(candidate["output_path"], cfg["data_dir"])
        key = "reels/" + candidate["output_hash"] + ".mp4"
        credentials = Vault(cfg).get("media-host") or {}
        access_key = os.environ.get("REEL_RELAY_S3_ACCESS_KEY_ID") or credentials.get("access_key_id")
        secret_key = os.environ.get("REEL_RELAY_S3_SECRET_ACCESS_KEY") or credentials.get("secret_access_key")
        if not access_key or not secret_key:
            raise RelayError(
                "Connect dedicated media-host credentials; personal AWS profiles are never used automatically"
            )
        client = boto3.client(
            "s3",
            endpoint_url=settings["s3_endpoint"] or None,
            region_name=settings["s3_region"],
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        # The destination bucket must already exist; this never creates public infrastructure.
        client.upload_file(
            str(path),
            settings["s3_bucket"],
            key,
            ExtraArgs={"ContentType": "video/mp4", "CacheControl": "public, max-age=86400"},
        )
        url = settings["public_media_base"].rstrip("/") + "/" + key
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise RelayError("Instagram needs a public HTTPS MP4 URL without embedded credentials")
    return url


def publish_one(cfg, cid=None, *, client=None, sleep=time.sleep, processing_timeout=300):
    if not cfg["publish"]["enabled"]:
        raise RelayError("Publishing is disabled; connect the intended account and enable it in config.toml")
    if cfg["publish"].get("dry_run", True):
        raise RelayError(
            "Preview mode is on. Disable publish.dry_run only when you intend to publish publicly"
        )
    store = Store(cfg)
    with store.lock("instagram-publisher", seconds=processing_timeout + 180) as guard:
        candidate = store.get(cid) if cid else store.next_publishable()
        if not candidate:
            return None
        cid = candidate["id"]
        if candidate["status"] == "published":
            return candidate
        if candidate["status"] in ("publishing", "publish_unknown"):
            # A timed-out POST may already have published. Never blindly send a second POST.
            raise RelayError("A previous publish attempt needs reconciliation; automatic retries are stopped")
        if candidate["status"] not in ("approved", "uploading", "container_ready"):
            raise RelayError("Only approved, rendered clips can be published")
        if candidate["is_test"]:
            raise RelayError("Test fixtures are permanently excluded from publication")
        if not (candidate.get("review") or {}).get("approved"):
            raise RelayError("Rendered preview has not passed review")
        path = inside(candidate["output_path"], cfg["data_dir"])
        if sha256(path) != candidate["output_hash"]:
            raise RelayError("The video changed after review; review the new export first")
        count, last = store.publication_window()
        if count >= cfg["publish"]["max_per_day"] or (
            last and time.time() - last < cfg["publish"]["min_interval_seconds"]
        ):
            return None
        try:
            client = client or Instagram(cfg)
        except (RelayError, TimeoutError) as exc:
            raise AccountUnavailable(str(exc)) from exc
        identity = client.token
        expected_username = cfg["publish"]["expected_username"]
        if not expected_username or identity.get("username", "").casefold() != expected_username.casefold():
            raise RelayError(
                "Confirm the intended Instagram username in publishing settings before enabling posts"
            )
        if (
            candidate["publishing_account_id"]
            and candidate["publishing_account_id"] != identity["account_id"]
        ):
            raise RelayError("This clip is bound to another Instagram account; reconnect that account")
        store.update(
            cid, expected=candidate["status"], guard=guard, publishing_account_id=identity["account_id"]
        )
        try:
            quota = client.limit().get("data", [])
        except (RelayError, TimeoutError) as exc:
            raise AccountUnavailable(str(exc)) from exc
        if quota:
            total = quota[0].get("config", {}).get("quota_total", 100)
            if quota[0].get("quota_usage", 0) >= total:
                return None
        caption = candidate["caption"] or ""
        if len(caption) > 2200:
            raise RelayError("Instagram caption exceeds 2,200 characters")
        if not candidate.get("container_id"):
            url = public_url(cfg, candidate)
            container_url = url
            store.update(cid, expected=candidate["status"], guard=guard, status="uploading", public_url=url)
            # Creating an unused container does not publish. Safe to retry if this call is interrupted.
            container_id = str(client.create(url, caption))
            store.update(
                cid, expected="uploading", guard=guard, status="container_ready", container_id=container_id
            )
        else:
            container_id = candidate["container_id"]
            container_url = candidate["public_url"]
        deadline = time.monotonic() + processing_timeout
        while time.monotonic() < deadline:
            if guard.lost.is_set():
                raise RelayError("Publisher ownership expired; another worker will resume this container")
            result = client.status(container_id)
            status = result.get("status_code")
            if status == "FINISHED":
                break
            if status == "PUBLISHED":
                store.update(
                    cid,
                    expected="container_ready",
                    guard=guard,
                    status="publish_unknown",
                    error="Container already published; verify media ID",
                )
                raise RelayError("Container is already published; reconcile its media ID before continuing")
            if status in ("ERROR", "EXPIRED"):
                current = store.get(cid)
                store.update(
                    cid,
                    expected="container_ready",
                    expected_version=current["updated_at"],
                    guard=guard,
                    status="publish_failed",
                    error="Instagram media processing failed: " + status,
                    metadata={
                        **current["metadata"],
                        "publication_failure": {
                            "phase": "container_processing",
                            "status": status,
                            "container_id": container_id,
                            "at": time.time(),
                        },
                    },
                )
                raise RelayError("Instagram media processing failed")
            sleep(min(60, max(0, deadline - time.monotonic())))
        else:
            # Resume polling the SAME container on the next run.
            store.update(
                cid,
                expected="container_ready",
                guard=guard,
                status="container_ready",
                error="Instagram is still processing this video",
            )
            return None
        if cfg.get("config_path"):
            current = load(cfg["config_path"])["publish"]
            if (
                not current["enabled"]
                or current["dry_run"]
                or current["expected_username"].casefold() != expected_username.casefold()
            ):
                raise RelayError("Publishing was paused or its intended account changed before publication")
        if sha256(path) != candidate["output_hash"]:
            raise RelayError("Reviewed export changed while Instagram was processing it")
        current = store.get(cid)
        if (
            current["is_test"]
            or not (current.get("review") or {}).get("approved")
            or current["output_hash"] != candidate["output_hash"]
            or current["caption"] != candidate["caption"]
            or current["container_id"] != container_id
            or current["public_url"] != container_url
            or current["publishing_account_id"] != identity["account_id"]
        ):
            raise RelayError("Approval or container inputs changed while Instagram was processing the export")
        store.reserve_publication(cid, guard, expected_version=current["updated_at"])
        try:
            media_id = str(client.publish(container_id))
        except Exception:
            # Reconciliation may already have confirmed publication while this
            # request was in flight. Never overwrite that confirmation.
            current = store.get(cid)
            if current["status"] == "publishing":
                store.update(
                    cid,
                    expected="publishing",
                    status="publish_unknown",
                    error="Publish response was not confirmed; do not retry blindly",
                )
            raise
        # Persist successful publication before any optional subsequent network call.
        store.update(cid, status="published", media_id=media_id, published_at=time.time())
        store.event("published", {"media_id": media_id}, cid)
        try:
            store.update(cid, permalink=client.permalink(media_id))
        except Exception as exc:  # noqa: BLE001 -- a missing optional permalink cannot undo publication
            store.event("permalink_lookup_failed", {"error_type": type(exc).__name__}, cid)
        return store.get(cid)


def reconcile(cfg, cid, *, client=None):
    store = Store(cfg)
    candidate = store.get(cid)
    if candidate["status"] not in ("publishing", "publish_unknown") or not candidate["container_id"]:
        raise RelayError("This candidate has no ambiguous publish attempt")
    client = client or Instagram(cfg)
    if (
        candidate["publishing_account_id"]
        and candidate["publishing_account_id"] != client.token["account_id"]
    ):
        raise RelayError("Reconnect the Instagram account used by this publish attempt")
    status = client.status(candidate["container_id"])["status_code"]
    if status == "PUBLISHED":
        store.update(
            cid,
            status="published",
            published_at=time.time(),
            error="Publication confirmed from container; permalink/media ID not yet retrieved",
        )
    else:
        # FINISHED alone does not establish that a timed-out publish request failed.
        store.event("publish_reconciliation", {"container_status": status, "needs_account_check": True}, cid)
    return store.get(cid)


def retry_failed_publication(cfg, cid, version):
    """Explicitly requeue unchanged reviewed media after a proven pre-send failure.

    This makes no remote request, never enables publishing, and cannot recover
    ambiguous publication. The next publisher pass creates a fresh container.
    """
    store = Store(cfg)
    with store.lock("instagram-publisher") as guard:
        item = store.get(cid)
        if item["is_test"] or not (item.get("review") or {}).get("approved"):
            raise RelayError("Only an approved real export can retry a failed upload")
        if (
            not item["output_path"]
            or sha256(inside(item["output_path"], cfg["data_dir"])) != item["output_hash"]
        ):
            raise RelayError("The export changed after review; review the correct version first")
        store.retry_publication(cid, guard, expected_version=version)
        return store.get(cid)


def _record_publish_error(store, candidate, error):
    """Best-effort diagnostics must never replace the original service failure."""
    account_failure = isinstance(error, AccountUnavailable)
    cid = candidate["id"] if candidate and not account_failure else None
    try:
        store.event("publisher_error", {"reason": str(error)[:300], "account_level": account_failure}, cid)
        if cid is None:
            return
        item = store.get(cid)
        if item["status"] in ("approved", "uploading", "container_ready"):
            failures = item["metadata"].get("publication_errors", 0) + 1
            # HTTP status alone cannot distinguish expired credentials from bad
            # media. Only an explicit ERROR/EXPIRED container status is terminal.
            store.update(
                cid,
                expected=item["status"],
                expected_version=item["updated_at"],
                error=str(error)[:300],
                metadata={**item["metadata"], "publication_errors": failures},
                available_at=time.time()
                + min(900, 30 * 2 ** min(failures - 1, 5)) * random.uniform(0.8, 1.2),
            )
    except Exception as exc:  # noqa: BLE001 -- a secondary DB failure must not terminate the watch loop
        print(f"Could not record publisher failure ({type(exc).__name__}); will retry monitoring", flush=True)


def publish_work(cfg, stop, *, watch=False):
    store = Store(cfg)
    while not stop.is_set():
        candidate = None
        try:
            if watch and cfg.get("config_path"):
                cfg = load(cfg["config_path"])
            candidate = store.next_publishable()
            if cfg["publish"]["enabled"] and not cfg["publish"]["dry_run"] and candidate:
                result = publish_one(cfg, candidate["id"])
                if result:
                    print(f"Published {result['id']}", flush=True)
        except LockBusy:
            # The active publisher owns the queue; this says nothing about the
            # candidate and must not mutate its retry count or availability.
            if not watch:
                raise
        except Exception as exc:
            print(f"Publishing needs attention: {exc}", flush=True)
            _record_publish_error(store, candidate, exc)
            if not watch:
                raise
        if not watch:
            return
        stop.wait(30)
