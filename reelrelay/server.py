"""FastAPI control plane. Capture, model calls and rendering run in separate workers."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field, SecretStr, ValidationError
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from . import __version__
from .auth import twitch_device_poll, twitch_device_start
from .config import ConfigurationConflict, StrictModel, channel_name, load, mutate
from .controls import control_service, installation_status, request_install, system_status
from .credentials import Vault
from .delivery import resolve_delivery
from .oauth import instagram_callback, instagram_start
from .product import activity, asset_records, readiness
from .publish import retry_failed_publication
from .setup import Setup, setup_snapshot
from .setup import save_setup as persist_setup
from .store import Store
from .uploads import MAX_UPLOAD_BYTES, add_asset, receive_upload
from .util import RelayError, inside
from .worker import (
    REVISION_STATES,
    approve,
    reassess,
    record_manual_post,
    record_operator_review,
    request_changes,
    retry,
)


def _same_secret(first, second):
    """Constant-time comparison also accepts malformed non-ASCII user input."""
    return secrets.compare_digest(first.encode("utf-8"), second.encode("utf-8"))


class JSONBodyLimit:
    """Enforce the actual JSON byte limit, including chunked requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] in {"GET", "HEAD", "OPTIONS"}
            or scope["path"] in {"/api/import", "/api/assets/upload"}
        ):
            return await self.app(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 256 * 1024:
                return await JSONResponse({"error": "JSON requests must be 256 KiB or smaller"}, 413)(
                    scope, receive, send
                )
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)


class CandidateAction(StrictModel):
    id: str = Field(pattern=r"^[a-f0-9]{16}$")


class VersionedAction(CandidateAction):
    version: float = Field(gt=0)


class Reassessment(VersionedAction):
    notes: str = Field("", max_length=1500)


class EditChanges(CandidateAction):
    notes: str = Field(min_length=3, max_length=1500)
    version: float = Field(gt=0)


class ManualPost(CandidateAction):
    permalink: str = Field(min_length=10, max_length=2000)
    version: float = Field(gt=0)
    confirmed: Literal[True]


class OperatorReview(EditChanges):
    watched: Literal[True]
    listened: Literal[True]


class Login(StrictModel):
    token: SecretStr


class ServiceControl(StrictModel):
    service: Literal["capture", "editor"]
    enabled: bool


class InstallRequest(StrictModel):
    model: Literal["tiny", "base", "small", "medium", "large-v3-turbo"]


class PublishingSettings(StrictModel):
    enabled: bool
    expected_username: str = Field("", max_length=64)
    auto_approve: bool = False


def create_app(cfg):
    app = FastAPI(title="Reel Relay", version=__version__, docs_url=None, redoc_url=None)
    app.add_middleware(JSONBodyLimit)
    app.state.cfg = cfg
    store, vault = Store(cfg), Vault(cfg)
    web = Path(__file__).parent / "web"
    remote = bool(cfg["web"]["public_base_url"]) or cfg["web"]["host"] not in (
        "localhost",
        "127.0.0.1",
        "::1",
    )
    access_token = os.environ.get("REEL_RELAY_WEB_TOKEN", "")
    public = urlparse(cfg["web"]["public_base_url"])
    if remote and (len(access_token) < 32 or public.scheme != "https" or not public.hostname):
        raise RelayError("Remote access requires an HTTPS public_base_url and a strong REEL_RELAY_WEB_TOKEN")
    hosts = {"localhost", "127.0.0.1", "::1"}
    if public.hostname:
        hosts.add(public.hostname)
    # Persisted across web workers; origin checks and session authentication are
    # independent from this token and protect all browser mutations.
    csrf = store.ensure_setting("web_csrf", secrets.token_urlsafe(32))

    def current_config():
        # All web processes see changes made by another process or the CLI.
        if cfg.get("config_path") and Path(cfg["config_path"]).is_file():
            app.state.cfg = load(cfg["config_path"])
        return app.state.cfg

    def valid_session(value):
        try:
            expiry, nonce, signature = value.split(".")
            body = expiry + "." + nonce
            expected = hmac.new(access_token.encode(), body.encode(), hashlib.sha256).hexdigest()
            return int(expiry) > time.time() and _same_secret(expected, signature)
        except (ValueError, AttributeError):
            return False

    def authenticated(request):
        if not remote:
            return True
        supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
        return (bool(supplied) and _same_secret(access_token, supplied)) or valid_session(
            request.cookies.get("relay_session", "")
        )

    def origin_allowed(request):
        origin = urlparse(request.headers.get("Origin", ""))
        if not origin.hostname or origin.hostname not in hosts:
            return False
        local_origin = f"http://{request.headers.get('host', '')}"
        return request.headers.get("Origin") in {local_origin, cfg["web"]["public_base_url"].rstrip("/")}

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.url.hostname not in hosts:
            return JSONResponse({"error": "Unrecognized host"}, 403)
        callback = request.url.path == "/auth/instagram/callback"
        login = request.url.path == "/api/login"
        public_route = (
            callback
            or login
            or request.url.path.startswith("/delivery/")
            or request.url.path.startswith("/assets/")
            or request.url.path
            in {"/", "/app.js", "/common.js", "/style.css", "/setup", "/setup.js", "/health/live"}
        )
        if not public_route and not authenticated(request):
            return JSONResponse({"error": "Sign in to this Relay server"}, 401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            # Explicit bearer clients use Authorization; browser requests also need
            # a same-origin check and CSRF token. No permissive CORS middleware.
            bearer = (
                remote
                and request.headers.get("Authorization", "").startswith("Bearer ")
                and authenticated(request)
            )
            if not bearer and (
                not origin_allowed(request)
                or (not login and not _same_secret(request.headers.get("X-Relay-Token", ""), csrf))
            ):
                return JSONResponse({"error": "Reload the review desk before making changes"}, 403)
            try:
                size = int(request.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            upload = request.url.path in {"/api/import", "/api/assets/upload"}
            limit = MAX_UPLOAD_BYTES if upload else 256 * 1024
            if not 0 <= size <= limit or (upload and not size):
                return JSONResponse({"error": "Invalid request size"}, 413)
        response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; img-src 'self'; media-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    @app.exception_handler(RelayError)
    async def relay_error(_, exc):
        return JSONResponse({"error": str(exc)}, 400)

    @app.exception_handler(ConfigurationConflict)
    async def settings_conflict(_, exc):
        return JSONResponse({"error": str(exc)}, 409)

    @app.exception_handler(RequestValidationError)
    @app.exception_handler(ValidationError)
    async def invalid_request(_, exc):
        # Pydantic's default JSON errors can echo credential inputs and context.
        details = [
            {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]} for error in exc.errors()
        ]
        return JSONResponse({"error": "Check the highlighted settings", "detail": details}, 422)

    @app.get("/health/live")
    def live():
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready")
    def ready():
        with store.connect() as c:
            c.execute(text("SELECT 1"))
        return {"status": "ready", "database": store.engine.dialect.name}

    @app.get("/")
    def index():
        return FileResponse(web / "index.html")

    if (web / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

    @app.get("/setup", include_in_schema=False)
    def setup_page():
        return FileResponse(web / "setup.html")

    @app.post("/api/login")
    def login(body: Login):
        if not remote or not _same_secret(body.token.get_secret_value(), access_token):
            return JSONResponse({"error": "Invalid server access token"}, 401)
        value = f"{int(time.time() + 43200)}.{secrets.token_urlsafe(24)}"
        signed = value + "." + hmac.new(access_token.encode(), value.encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({"ok": True})
        response.set_cookie(
            "relay_session", signed, httponly=True, secure=True, samesite="strict", max_age=43200
        )
        return response

    @app.get("/api/state")
    def state():
        current = current_config()
        snapshot = store.snapshot()
        # Raw operational events can contain upstream URLs and error details.
        # The browser receives a bounded, purpose-built activity projection.
        snapshot["events"] = activity(store, 30)["items"]
        allowed = {
            "id",
            "channel",
            "status",
            "created_at",
            "updated_at",
            "score",
            "confidence",
            "caption",
            "review",
            "decision",
            "error",
            "is_test",
            "start",
            "end",
            "permalink",
        }
        snapshot["candidates"] = [
            {
                **{k: v for k, v in item.items() if k in allowed},
                "has_export": bool(item["output_path"]),
                "has_evidence": bool(item["evidence_path"]),
                "can_request_changes": bool(item["output_path"]) and item["status"] in REVISION_STATES,
                "posted_manually": item["metadata"].get("publication_method") == "manual",
                "export_version": item["output_hash"],
                "origin": "synthetic"
                if item["is_test"]
                else (
                    "manual_import"
                    if item["metadata"].get("origin") == "user-selected local file"
                    else "live_chat_spike"
                ),
            }
            for item in snapshot["candidates"]
        ]
        snapshot["settings"] = {
            "publishing": current["publish"]["enabled"] and not current["publish"]["dry_run"],
            "dry_run": current["publish"]["dry_run"],
            "automatic": current["publish"]["auto_approve"],
            "daily_limit": current["publish"]["max_per_day"],
            "provider": current["agent"]["provider"],
            "csrf": csrf,
        }
        snapshot["accounts"] = {name: vault.exists(name) for name in ("twitch", "instagram")}
        return snapshot

    @app.get("/api/readiness")
    def workspace_readiness():
        return readiness(current_config())

    @app.get("/api/activity")
    def workspace_activity(limit: int = Query(50, ge=1, le=100), before: int | None = Query(None, gt=0)):
        return activity(store, limit, before)

    @app.get("/api/assets")
    def library():
        items = [{k: v for k, v in row.items() if k != "_path"} for row in asset_records(current_config())]
        return {
            "items": items,
            "total": len(items),
            "warnings": [] if items else ["No local assets added yet"],
        }

    @app.get("/api/assets/{asset_id}/preview")
    def asset_preview(asset_id: str):
        for row in asset_records(current_config()):
            if _same_secret(row["id"], asset_id):
                return FileResponse(row["_path"])
        return JSONResponse({"error": "Asset not found"}, 404)

    @app.get("/api/setup")
    def setup():
        return setup_snapshot(current_config(), csrf)

    @app.post("/api/setup")
    def save_setup(body: Setup):
        current, result = persist_setup(current_config(), body)
        result["restart_required"] = current["web"]["public_base_url"] != cfg["web"]["public_base_url"]
        result["message"] = (
            "Saved. Restart the server to apply its public origin and access policy."
            if result["restart_required"]
            else "Saved. Running workers apply changes to the next job; capture settings reload automatically."
        )
        app.state.cfg = current
        store.event("configuration_updated", {"restart_required": result["restart_required"]})
        return result

    @app.get("/api/system")
    def system():
        return system_status(current_config())

    @app.post("/api/system/control")
    def workflow_control(body: ServiceControl):
        return control_service(current_config(), body.service, body.enabled)

    @app.get("/api/system/install")
    def install_status():
        return installation_status(current_config())

    @app.post("/api/system/install")
    def install(body: InstallRequest):
        return request_install(current_config(), body.model)

    @app.post("/api/import")
    async def import_clip(
        request: Request,
        filename: str = Query(max_length=255),
        channel: str = Query("local", max_length=25),
        attribution: str = Query("", max_length=400),
    ):
        from .cli import import_file

        try:
            channel = channel_name(channel)
        except ValueError as exc:
            raise RelayError(str(exc)) from None
        current = current_config()
        target = await receive_upload(request, current, filename)
        try:
            cid = await run_in_threadpool(
                import_file, current, target, channel=channel, attribution=attribution
            )
            return {"id": cid}
        finally:
            target.unlink(missing_ok=True)

    @app.post("/api/assets/upload")
    async def upload_asset(
        request: Request,
        filename: str = Query(max_length=255),
        title: str = Query(min_length=1, max_length=200),
        kind: Literal["sfx", "meme", "broll"] = Query(),
        rights: str = Query(min_length=1, max_length=500),
        attribution: str = Query("", max_length=400),
        tags: str = Query("", max_length=1000),
    ):
        current = current_config()
        target = await receive_upload(request, current, filename, asset=True)
        try:
            return await run_in_threadpool(
                add_asset,
                current,
                target,
                title=title,
                kind=kind,
                rights=rights,
                attribution=attribution,
                tags=[tag.strip()[:80] for tag in tags.split(",") if tag.strip()][:20],
            )
        finally:
            target.unlink(missing_ok=True)

    @app.post("/api/publishing")
    def publishing_settings(body: PublishingSettings):
        current = current_config()
        if body.enabled:
            account = vault.account_status("instagram")
            if (
                not account["connected"]
                or not body.expected_username
                or (account.get("username") or "").casefold() != body.expected_username.casefold()
            ):
                raise RelayError("Connect the intended Instagram account and enter its exact username")
            if not current["web"]["public_base_url"] and not (
                current["publish"]["public_media_base"] and current["publish"]["s3_bucket"]
            ):
                raise RelayError("Configure HTTPS video delivery before enabling publishing")

        def change(latest):
            latest["publish"].update(
                enabled=body.enabled,
                dry_run=not body.enabled,
                expected_username=body.expected_username,
                auto_approve=body.auto_approve,
            )

        current = mutate(cfg["config_path"], change)
        app.state.cfg = current
        store.event(
            "publishing_settings_changed", {"enabled": body.enabled, "auto_approve": body.auto_approve}
        )
        return {"ok": True}

    @app.post("/api/auth/twitch/start")
    def twitch_connect():
        current = current_config()
        return twitch_device_start(current, current["auth"]["twitch_client_id"])

    @app.post("/api/auth/twitch/poll")
    def twitch_poll():
        return twitch_device_poll(current_config())

    @app.post("/api/auth/instagram/start")
    def instagram_connect():
        binding = secrets.token_urlsafe(32)
        response = JSONResponse({"url": instagram_start(current_config(), binding)})
        response.set_cookie("relay_oauth", binding, httponly=True, secure=remote, samesite="lax", max_age=600)
        return response

    @app.get("/auth/instagram/callback", include_in_schema=False)
    def instagram_return(
        request: Request, state: str = "", code: str | None = None, error: str | None = None
    ):
        instagram_callback(
            current_config(),
            state=state,
            binding=request.cookies.get("relay_oauth", ""),
            code=code,
            error=error,
        )
        response = RedirectResponse("/setup?connected=instagram", status_code=303)
        response.delete_cookie("relay_oauth")
        return response

    @app.head("/media/{cid}/{kind}", include_in_schema=False)
    @app.get("/media/{cid}/{kind}")
    def media(
        cid: str, kind: Literal["video", "source", "poster", "contact", "credits"], download: bool = False
    ):
        item = store.get(cid)
        if kind in ("video", "source"):
            target = item["output_path"] if kind == "video" and item["output_path"] else item["source_path"]
        elif item["output_path"]:
            target = (
                Path(item["output_path"]).parent
                / {"poster": "poster.jpg", "contact": "contact-sheet.jpg", "credits": "credits.txt"}[kind]
            )
        elif kind in ("poster", "contact") and item["evidence_path"]:
            target = Path(item["evidence_path"]).parent / "source-0.jpg"
        else:
            target = None
        if not target or not inside(target, cfg["data_dir"]).is_file():
            return JSONResponse({"error": "Media is not ready"}, 404)
        return FileResponse(target, filename=Path(target).name if download else None)

    @app.head("/delivery/{cid}.mp4", include_in_schema=False)
    @app.get("/delivery/{cid}.mp4", include_in_schema=False)
    def delivery(cid: str, expires: int, signature: str):
        try:
            path = resolve_delivery(current_config(), cid, expires, signature)
        except RelayError:
            return JSONResponse({"error": "Media delivery link is invalid or expired"}, 403)
        return FileResponse(path, media_type="video/mp4")

    @app.post("/api/approve")
    def approve_clip(body: VersionedAction):
        approve(current_config(), body.id, body.version)
        return {"ok": True}

    @app.post("/api/retry")
    def retry_clip(body: CandidateAction):
        retry(current_config(), body.id)
        return {"ok": True}

    @app.post("/api/retry-publication")
    def retry_publication(body: VersionedAction):
        retry_failed_publication(current_config(), body.id, body.version)
        return {"ok": True}

    @app.post("/api/reassess")
    def reassess_clip(body: Reassessment):
        reassess(current_config(), body.id, body.version, body.notes)
        return {"ok": True}

    @app.post("/api/request-changes")
    def revise_clip(body: EditChanges):
        request_changes(current_config(), body.id, body.notes, body.version)
        return {"ok": True}

    @app.post("/api/record-manual-post")
    def record_post(body: ManualPost):
        record_manual_post(current_config(), body.id, body.permalink, body.version)
        return {"ok": True, "api_verified": False}

    @app.post("/api/operator-review")
    def operator_review(body: OperatorReview):
        record_operator_review(current_config(), body.id, body.notes, body.version)
        return {"ok": True}

    @app.post("/api/reject")
    def reject_clip(body: VersionedAction):
        item = store.get(body.id)
        if item["status"] not in {
            "captured",
            "needs_agent",
            "editorial_review",
            "selected",
            "ready",
            "approved",
            "failed",
            "preview_changes",
            "needs_preview_review",
            "needs_human_review",
        }:
            raise RelayError("Wait for the current operation to finish")
        store.update(body.id, expected=item["status"], expected_version=body.version, status="rejected")
        return {"ok": True}

    return app


def serve(cfg, port=None):
    app = create_app(cfg)
    # Authorization codes and query parameters must never appear in access logs.
    uvicorn.run(
        app,
        host=cfg["web"]["host"],
        port=port or cfg["web"]["port"],
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )
