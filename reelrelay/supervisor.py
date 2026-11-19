"""A small deterministic process supervisor; model calls stay in editing jobs."""

from __future__ import annotations

import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from .controls import service_enabled
from .store import Store


def supervise(cfg, args, stop, *, web=True):
    store = Store(cfg)
    roles = {
        "capture": ["run", "--watch-config"],
        "editor": ["worker", "--watch"],
        "publisher": ["publisher", "--watch"],
    }
    port = getattr(args, "port", None) or cfg["web"]["port"]
    if web:
        roles["web"] = ["serve", "--port", str(port)]
    children, retry_at, failures, started_at = {}, {}, {}, {}
    deadline = time.monotonic() + args.duration if getattr(args, "duration", None) else None
    base = [sys.executable, "-B", "-m", "reelrelay", "--config", cfg["config_path"]]

    def start(name):
        child = subprocess.Popen(
            base + roles[name],
            start_new_session=True,
            **({"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT} if not web else {}),
        )
        children[name] = child
        if not web:
            folder = Path(cfg["data_dir"]) / ".private" / "services"
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = folder / (name + ".log")
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=4 * 1024**2, backupCount=1)
            path.chmod(0o600)

            def drain():
                try:
                    while chunk := child.stdout.read1(4096):
                        handler.emit(
                            logging.LogRecord(
                                name,
                                logging.INFO,
                                "",
                                0,
                                chunk.decode("utf-8", errors="replace").rstrip(),
                                (),
                                None,
                            )
                        )
                finally:
                    child.stdout.close()
                    handler.close()

            threading.Thread(target=drain, daemon=True).start()
        started_at[name] = time.monotonic()

    def signal_group(child, sig):
        # Descendants may survive a dead group leader; these groups were created
        # by this supervisor and must be reaped before replacement or shutdown.
        try:
            os.killpg(child.pid, sig)
        except ProcessLookupError:
            pass

    with store.lock("workflow-supervisor", seconds=45) as guard:
        try:
            for name in roles:
                start(name)
            print(f"Workflow services started. http://127.0.0.1:{port}", flush=True)
            if web and getattr(args, "open", False):
                import webbrowser

                webbrowser.open(f"http://127.0.0.1:{port}")
            last_health = 0
            while not stop.wait(1):
                if guard.lost.is_set():
                    break
                now = time.monotonic()
                if deadline is not None and now >= deadline:
                    break
                for name, child in list(children.items()):
                    if child.poll() is not None:
                        if name not in retry_at:
                            signal_group(child, signal.SIGINT)
                            failures[name] = 1 if now - started_at[name] > 60 else failures.get(name, 0) + 1
                            retry_at[name] = now + min(30, 2 ** min(failures[name], 5))
                            store.event(
                                "service_restart_scheduled", {"service": name, "exit_code": child.returncode}
                            )
                        if now >= retry_at[name]:
                            signal_group(child, signal.SIGKILL)
                            start(name)
                            retry_at.pop(name)
                if now - last_health >= 2:
                    store.health("@service:supervisor", state="running", pid=os.getpid())
                    busy = bool(store.list({"preparing", "judging", "rendering", "reviewing", "revising"}, 1))
                    for name in ("capture", "editor"):
                        enabled = service_enabled(cfg, name)
                        state = (
                            "starting"
                            if name in retry_at
                            else "running"
                            if enabled
                            else "pausing"
                            if name == "editor" and busy
                            else "paused"
                        )
                        store.health("@service:" + name, state=state, pid=children[name].pid)
                    last_health = now
        finally:
            for child in children.values():
                signal_group(child, signal.SIGINT)
            for child in children.values():
                try:
                    child.wait(timeout=25)
                except subprocess.TimeoutExpired:
                    signal_group(child, signal.SIGKILL)
                    child.wait()
                signal_group(child, signal.SIGKILL)
            if not guard.lost.is_set():
                for name in ("capture", "editor", "supervisor"):
                    store.health("@service:" + name, state="stopped")
