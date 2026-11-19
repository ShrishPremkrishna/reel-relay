from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from . import __version__, media
from .auth import instagram_import, twitch_login
from .capture import monitor, monitor_config
from .config import channel_name, load
from .credentials import Vault
from .install import setup_media
from .product import readiness
from .publish import publish_one, publish_work, reconcile
from .store import Store
from .studio import Studio
from .supervisor import supervise
from .util import RelayError, read_json, sha256
from .worker import apply_decision, apply_review, approve, retry, work


def import_file(cfg, source, *, channel="local", attribution="", is_test=False):
    source = Path(source).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        raise RelayError("Import a local MP4, MOV, MKV or WebM file")
    if source.stat().st_size > 2 * 1024**3:
        raise RelayError("Import clips under 2 GB; slice long streams before importing")
    destination = Path(cfg["data_dir"]) / "sources" / (uuid.uuid4().hex + source.suffix.lower())
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, destination)
        info = media.probe(destination, cfg["data_dir"])
        source_hash = sha256(destination)
        store = Store(cfg)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    # Do not delete on an ambiguous database commit failure: the file may already
    # be referenced by a committed candidate. Successful deduplication is explicit.
    cid = store.add(
        channel,
        destination,
        source_hash,
        end=info["duration"],
        metadata={"origin": "user-selected local file", "original_name": source.name},
        attribution=attribution,
        is_test=is_test,
    )
    if Path(store.get(cid)["source_path"]).resolve() != destination.resolve():
        destination.unlink(missing_ok=True)
    return cid


def doctor(cfg):
    result = {
        "version": __version__,
        "python": sys.version.split()[0],
        "data_dir": cfg["data_dir"],
        "media_engine": cfg["media"]["engine"],
        "agent_provider": cfg["agent"]["provider"],
        "publishing_enabled": cfg["publish"]["enabled"],
        "max_posts_per_day": cfg["publish"]["max_per_day"],
    }
    for package in ("streamlink", "websocket-client"):
        try:
            result[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result[package] = "not installed"
    for name in ("ffmpeg", "ffprobe"):
        try:
            result[name] = media.binary(name)
        except RelayError:
            result[name] = "not installed"
    for service in ("twitch", "instagram"):
        account = Vault(cfg).account_status(service)
        result[service + "_connected"] = account["connected"]
        if account["connected"]:
            result[service + "_account"] = account.get("username")
    result["codex_available"] = shutil.which("codex") is not None
    result["database"] = Store(cfg).engine.dialect.name
    result["free_disk_gb"] = round(shutil.disk_usage(cfg["data_dir"]).free / 1024**3, 2)
    result["preview_mode"] = cfg["publish"]["dry_run"]
    result["readiness"] = readiness(cfg)
    return result


def main():
    parser = argparse.ArgumentParser(description="Reel Relay — live moments to reviewed Reels")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Create the local configuration")
    p = sub.add_parser(
        "setup-media", help="Check or explicitly install local transcription dependencies/model"
    )
    p.add_argument("--download-model", choices=["tiny", "base", "small", "medium", "large-v3-turbo"])
    sub.add_parser("doctor", help="Check local tools and account connections")
    sub.add_parser("setup-studio", help="Stage a private isolated copy of your existing editing engine")
    p = sub.add_parser("run", help="Record enabled Twitch channels and detect chat spikes")
    p.add_argument("--duration", type=float)
    p.add_argument("--channel", type=channel_name)
    p.add_argument("--watch-config", action="store_true", help="Apply saved channel changes automatically")
    p.add_argument(
        "--mark-after", type=float, help="Save one test window this many seconds after video begins"
    )
    p = sub.add_parser("start", help="Start capture, editing worker and review desk together")
    p.add_argument("--duration", type=float)
    p.add_argument("--port", type=int)
    p.add_argument("--open", action="store_true", help="Open the local browser interface")
    p = sub.add_parser("services", help="Supervise capture/editor/publisher behind an existing UI")
    p.add_argument("--duration", type=float)
    p = sub.add_parser("install-job", help=argparse.SUPPRESS)
    p.add_argument("--model", required=True, choices=["tiny", "base", "small", "medium", "large-v3-turbo"])
    p.add_argument("--job-id", required=True)
    p = sub.add_parser("worker", help="Prepare, assess, render and review queued candidates")
    p.add_argument("--watch", action="store_true")
    p.add_argument("--id")
    p.add_argument("--stage", choices=["preparing", "judging", "rendering", "reviewing", "revising"])
    p = sub.add_parser("publisher", help="Process the publishing queue independently of editing")
    p.add_argument("--watch", action="store_true")
    p = sub.add_parser("import", help="Import a selected local clip")
    p.add_argument("file")
    p.add_argument("--channel", default="local")
    p.add_argument("--attribution", default="")
    p = sub.add_parser("decision", help="Apply a provider-independent JSON editorial decision")
    p.add_argument("id")
    p.add_argument("file")
    p = sub.add_parser("review", help="Apply a rendered-preview JSON review")
    p.add_argument("id")
    p.add_argument("file")
    for command in ("approve", "retry", "reject", "reconcile"):
        p = sub.add_parser(command)
        p.add_argument("id")
    p = sub.add_parser("status")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("serve", help="Open the local review desk")
    p.add_argument("--port", type=int)
    p = sub.add_parser("publish", help="Publish one approved clip to the connected account")
    p.add_argument("id")
    p.add_argument("--media-url")
    p = sub.add_parser("auth")
    a = p.add_subparsers(dest="service", required=True)
    t = a.add_parser("twitch")
    t.add_argument("--client-id", required=True)
    i = a.add_parser("instagram")
    i.add_argument("--token-file", required=True)
    p = sub.add_parser("demo", help="Generate a clearly labeled synthetic capture/edit test")
    p.add_argument("--render", action="store_true")
    args = parser.parse_args()
    stop = threading.Event()

    def interrupt(*_):
        stop.set()

    if args.command in ("run", "start", "services", "worker", "publisher", "auth"):
        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
    try:
        if args.command == "init":
            target = Path(args.config)
            if target.exists():
                raise RelayError("Config already exists; it was not overwritten")
            example = Path(__file__).parent / "config.example.toml"
            if not example.is_file():
                example = Path(__file__).resolve().parents[1] / "config.example.toml"
            shutil.copy2(example, target)
            fresh = Store(load(target))
            fresh.set_setting("workflow:capture", False)
            fresh.set_setting("workflow:editor", False)
            print(f"Created {target.resolve()}")
            return
        cfg = load(args.config)
        store = Store(cfg)
        if args.command == "doctor":
            print(json.dumps(doctor(cfg), indent=2))
        elif args.command == "setup-studio":
            print(Studio(cfg).stage())
        elif args.command == "setup-media":
            print(
                json.dumps(
                    setup_media(cfg, args.download_model or "small", download=bool(args.download_model)),
                    indent=2,
                )
            )
        elif args.command == "run":
            if args.channel:
                cfg["channels"] = [{"name": args.channel, "enabled": True, "chat": "anonymous-irc"}]
            if args.watch_config:
                if args.channel or args.mark_after is not None:
                    raise RelayError(
                        "Use saved channels with --watch-config; use a separate bounded run for test markers"
                    )
                monitor_config(cfg, stop, duration=args.duration)
            else:
                monitor(cfg, stop, duration=args.duration, mark_after=args.mark_after)
        elif args.command == "start":
            supervise(cfg, args, stop)
        elif args.command == "services":
            supervise(cfg, args, stop, web=False)
        elif args.command == "install-job":
            from .controls import run_install_job

            run_install_job(cfg, args.model, args.job_id)
        elif args.command == "worker":
            work(cfg, stop, watch=args.watch, cid=args.id, stage=args.stage)
        elif args.command == "publisher":
            publish_work(cfg, stop, watch=args.watch)
        elif args.command == "import":
            print(import_file(cfg, args.file, channel=args.channel, attribution=args.attribution))
        elif args.command == "decision":
            print(apply_decision(cfg, args.id, read_json(args.file))["status"])
        elif args.command == "review":
            print(apply_review(cfg, args.id, read_json(args.file))["status"])
        elif args.command == "approve":
            approve(cfg, args.id)
        elif args.command == "retry":
            retry(cfg, args.id)
        elif args.command == "reject":
            item = store.get(args.id)
            if item["status"] not in (
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
            ):
                raise RelayError("Wait for the current operation to finish before rejecting this clip")
            store.update(args.id, expected=item["status"], status="rejected")
        elif args.command == "status":
            if args.json:
                print(json.dumps(store.snapshot(), indent=2))
            else:
                print("ID                CHANNEL          STATE                   SCORE")
                for item in store.list():
                    print(f"{item['id']:18} {item['channel']:16} {item['status']:23} {item['score'] or '-'}")
        elif args.command == "publish":
            if args.media_url:
                store.update(args.id, public_url=args.media_url)
            result = publish_one(cfg, args.id)
            print(
                result.get("permalink") or result["status"]
                if result
                else "Queued until the next posting slot"
            )
        elif args.command == "reconcile":
            print(reconcile(cfg, args.id)["status"])
        elif args.command == "serve":
            from .server import serve

            serve(cfg, args.port)
        elif args.command == "auth":
            if args.service == "twitch":
                twitch_login(cfg, args.client_id, stop=stop)
            else:
                print(json.dumps(instagram_import(cfg, args.token_file)))
        elif args.command == "demo":
            from .demo import demo

            print(json.dumps(demo(cfg, render=args.render), indent=2))
    except (RelayError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Reel Relay: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass
