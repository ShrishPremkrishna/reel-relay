from __future__ import annotations

import csv
import math
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack
from pathlib import Path

from . import media
from .config import load
from .controls import service_enabled
from .detector import ChatMessage, SpikeDetector
from .store import Store
from .twitch import anonymous_chat, eventsub_chat
from .util import RelayError, sha256, write_json

# Only work that an editing worker can advance consumes processing capacity.
# Review holds and the publishing queue have independent lifecycles; retaining
# them must not stop capture when posting is paused or a reviewer is absent.
PROCESSING_STATES = (
    "captured",
    "preparing",
    "needs_agent",
    "judging",
    "selected",
    "rendering",
    "needs_preview_review",
    "reviewing",
    "preview_changes",
    "revising",
)


def intake_status(store, cap, pending_count):
    limit = cap["max_pending"]
    backlog = len(store.list(PROCESSING_STATES, limit))
    return {
        "processing_backlog": backlog,
        "processing_limit": limit,
        "intake_state": (
            "pending_windows_full"
            if pending_count >= limit
            else "processing_backlog_full"
            if backlog >= limit
            else "accepting"
        ),
    }


def free_space_ok(cfg):
    root = Path(cfg["data_dir"])
    root.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(root).free >= cfg["capture"]["min_free_gb"] * 1024**3


class Recorder:
    def __init__(self, cfg, channel, *, source=None, realtime=True):
        self.cfg, self.channel = cfg, channel
        self.root = Path(cfg["data_dir"])
        self.session_id = uuid.uuid4().hex
        self.folder = self.root / "recordings" / channel / self.session_id
        self.folder.mkdir(parents=True)
        self.csv_path = self.folder / "segments.csv"
        self.started_at = None
        self.source, self.realtime = source, realtime
        self.seen = {}
        self.capture = self.segmenter = self.pump = None
        self.log = None
        self._process_lock = threading.RLock()
        self._stopping = False

    def start(self):
        # Shutdown may arrive while this recorder is being registered/started.
        # Serialize process creation with stop so no child appears after cleanup.
        with self._process_lock:
            if self._stopping:
                raise RelayError("Recorder was stopped before startup")
            self._start()

    def _start(self):
        if not free_space_ok(self.cfg):
            raise RelayError("Capture paused: free disk space is below the configured reserve")
        # The monitor holds this channel's lease. Only leftover ephemeral buffers
        # from older owned sessions are removed, never extracted source clips.
        for folder in self.folder.parent.iterdir():
            if folder == self.folder or folder.is_symlink() or not folder.is_dir():
                continue
            if len(folder.name) == 32 and all(c in "0123456789abcdef" for c in folder.name):
                for path in folder.glob("segment-*.ts"):
                    path.unlink(missing_ok=True)
                (folder / "segments.csv").unlink(missing_ok=True)
        args = [media.binary("ffmpeg"), "-hide_banner", "-v", "warning", "-nostdin", "-n"]
        if self.source:
            source = media.inside(self.source, self.root)
            if self.realtime:
                args += ["-re"]
            args += ["-protocol_whitelist", "file,pipe", "-i", str(source)]
            self.started_at = time.time()
        else:
            streamlink = shutil.which("streamlink") or str(Path(sys.executable).parent / "streamlink")
            if not Path(streamlink).is_file():
                raise RelayError("Install the live dependencies before capturing Twitch")
            self.log = (self.folder / "capture.log").open("ab")
            self.capture = subprocess.Popen(
                [
                    streamlink,
                    "--no-config",
                    "--no-plugin-sideloading",
                    "--loglevel",
                    "warning",
                    "--stdout",
                    "--hls-live-edge",
                    "2",
                    "--stream-timeout",
                    "30",
                    "https://www.twitch.tv/" + self.channel,
                    self.cfg["capture"]["quality"],
                ],
                stdout=subprocess.PIPE,
                stderr=self.log,
                start_new_session=True,
            )
            args += ["-protocol_whitelist", "file,pipe", "-i", "pipe:0"]
        args += [
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self.cfg["capture"]["segment_seconds"]),
            "-reset_timestamps",
            "1",
            "-segment_list",
            str(self.csv_path),
            "-segment_list_type",
            "csv",
            "-segment_list_size",
            str(
                math.ceil(self.cfg["capture"]["retention_seconds"] / self.cfg["capture"]["segment_seconds"])
                + 30
            ),
            "-segment_format",
            "mpegts",
            str(self.folder / "segment-%08d.ts"),
        ]
        self.media_log = (self.folder / "segments.log").open("ab")
        self.segmenter = subprocess.Popen(
            media.command(args, self.root),
            stdin=subprocess.PIPE if not self.source else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=self.media_log,
            env=media.environment(self.root),
            start_new_session=True,
        )
        if self.capture:
            self.pump = threading.Thread(target=self._pump, daemon=True)
            self.pump.start()
        write_json(
            self.folder / "session.json",
            {
                "id": self.session_id,
                "channel": self.channel,
                "source": "local replay" if self.source else "https://www.twitch.tv/" + self.channel,
                "created_at": time.time(),
                "timing": "media timestamps plus arrival-time anchor; calibrate offset",
            },
        )

    def _pump(self):
        try:
            while chunk := self.capture.stdout.read1(65536):
                if self.started_at is None:
                    self.started_at = time.time()
                self.segmenter.stdin.write(chunk)
        except (BrokenPipeError, ValueError, OSError):
            pass
        finally:
            try:
                self.segmenter.stdin.close()
            except (OSError, ValueError):
                pass

    def segments(self):
        rows = []
        if not self.csv_path.exists():
            return rows
        for row in csv.reader(self.csv_path.read_text().splitlines()):
            if len(row) != 3:
                continue
            path = self.folder / Path(row[0]).name
            if not path.is_file():
                continue
            try:
                start, end = float(row[1]), float(row[2])
            except ValueError:
                continue
            if not math.isfinite(start) or not math.isfinite(end) or end <= start:
                continue
            self.seen.setdefault(path.name, time.time())
            rows.append({"path": str(path), "start": start, "end": end, "closed_at": self.seen[path.name]})
        names = {Path(row["path"]).name for row in rows}
        self.seen = {key: value for key, value in self.seen.items() if key in names}
        return rows

    def media_time(self, timestamp, segments):
        if segments:
            last = segments[-1]
            return last["end"] + (timestamp - last["closed_at"])
        return timestamp - (self.started_at or timestamp)

    def prune(self, segments, protect_from=None):
        if not segments:
            return
        cutoff = segments[-1]["end"] - self.cfg["capture"]["retention_seconds"]
        if protect_from is not None:
            cutoff = min(cutoff, protect_from)
        for segment in segments:
            if segment["end"] < cutoff:
                Path(segment["path"]).unlink(missing_ok=True)
        # The segment list itself is a bounded ring. Remove older files which
        # have rolled out of that ring, never its currently open final segment.
        first = min((Path(s["path"]).name for s in segments), default="")
        for path in self.folder.glob("segment-*.ts"):
            if path.name < first:
                path.unlink(missing_ok=True)
        for handle in (self.log, getattr(self, "media_log", None)):
            if handle and not handle.closed and os.fstat(handle.fileno()).st_size > 2 * 1024**2:
                # O_APPEND handles remain valid after truncation.
                os.ftruncate(handle.fileno(), 0)

    def release_buffer(self):
        """Ephemeral stream data only; extracted candidate originals are separate."""
        for path in self.folder.glob("segment-*.ts"):
            path.unlink(missing_ok=True)
        self.csv_path.unlink(missing_ok=True)
        # Bound diagnostic session history across repeated offline reconnects.
        sessions = sorted(
            (
                p
                for p in self.folder.parent.iterdir()
                if p.is_dir() and len(p.name) == 32 and all(c in "0123456789abcdef" for c in p.name)
            ),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for folder in sessions[8:]:
            if folder != self.folder and not folder.is_symlink():
                shutil.rmtree(folder)

    def alive(self):
        return self.segmenter is not None and self.segmenter.poll() is None

    def stop(self):
        with self._process_lock:
            self._stopping = True
            self._stop()

    def _stop(self):
        # Stop only processes started by this recorder. SIGINT lets ffmpeg seal its last segment.
        for process in (self.capture, self.segmenter):
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                    process.wait(timeout=8)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
        if self.pump:
            self.pump.join(timeout=2)
        for process in (self.capture, self.segmenter):
            if process:
                for pipe in (process.stdin, process.stdout, process.stderr):
                    if pipe and not pipe.closed:
                        pipe.close()
        for handle in (self.log, getattr(self, "media_log", None)):
            if handle:
                handle.close()


class RecorderGroup:
    """Track owned recorders independently of threads doing slow extraction."""

    def __init__(self):
        self.lock = threading.Lock()
        self.recorders = set()
        self.stopping = False

    def add(self, recorder):
        with self.lock:
            if self.stopping:
                return False
            self.recorders.add(recorder)
            return True

    def discard(self, recorder):
        with self.lock:
            self.recorders.discard(recorder)

    def stop(self):
        with self.lock:
            self.stopping = True
            recorders = list(self.recorders)
        # Stop all channels concurrently; one slow child must not leave the other
        # channels running until the supervisor's shutdown timeout kills us.
        errors = []

        def close(recorder):
            try:
                recorder.stop()
            except Exception as exc:  # noqa: BLE001 -- stop every owned recorder, then propagate the failure
                errors.append(exc)

        closers = [threading.Thread(target=close, args=(recorder,)) for recorder in recorders]
        for closer in closers:
            closer.start()
        for closer in closers:
            closer.join()
        if errors:
            raise errors[0]


def capture_channel(
    cfg,
    channel,
    messages,
    stop,
    store,
    *,
    source=None,
    replay=None,
    mark_after=None,
    guard=None,
    recorders=None,
):
    name, cap = channel["name"], cfg["capture"]
    replay = replay or []
    while not stop.is_set():
        recorder = Recorder(cfg, name, source=source)
        if recorders is not None and not recorders.add(recorder):
            recorder.stop()
            break
        detector, pending, total, replay_index = SpikeDetector(cfg["detector"]), [], 0, 0
        started, marked, last_health = time.time(), False, 0
        previous_clock, previous_monotonic = time.time(), time.monotonic()

        def save_window(item, segments, *, truncated=False, recorder=recorder, pending=pending):
            output = Path(cfg["data_dir"]) / "sources" / (uuid.uuid4().hex + ".mp4")
            actual_start, actual_end = media.extract(
                segments, item["start"], item["end"], output, cfg["data_dir"]
            )
            cid = store.add(
                name,
                output,
                sha256(output),
                session_id=recorder.session_id,
                start=actual_start,
                end=actual_end,
                metadata={
                    "trigger": item["trigger"],
                    "timing_offset_seconds": cap["chat_video_offset_seconds"],
                    "postroll_truncated": truncated,
                },
                attribution=channel.get("attribution", f"Source: twitch.tv/{name}"),
                guard=guard,
            )
            if Path(store.get(cid)["source_path"]).resolve() != output.resolve():
                output.unlink(missing_ok=True)
            store.health(name, last_candidate=cid)
            pending.remove(item)

        try:
            recorder.start()
            store.health(
                name,
                capture_state="connecting",
                session_id=recorder.session_id,
                timing_offset_seconds=cap["chat_video_offset_seconds"],
                intake_state="waiting_for_video",
                capture_error=None,
            )
            while not stop.is_set():
                now = time.time()
                # A system clock jump invalidates arrival-time alignment; start a new session.
                monotonic = time.monotonic()
                if abs((now - previous_clock) - (monotonic - previous_monotonic)) > 2:
                    raise RelayError("System clock changed; resetting capture timing")
                previous_clock, previous_monotonic = now, monotonic
                if not free_space_ok(cfg):
                    raise RelayError("Capture paused: free disk space is below the configured reserve")
                segments = recorder.segments()
                while replay_index < len(replay) and replay[replay_index]["timestamp"] <= now - started:
                    item = replay[replay_index]
                    messages.put(
                        ChatMessage(started + item["timestamp"], item["user"], item["text"], item["id"])
                    )
                    replay_index += 1
                triggers = []
                for _ in range(50000):
                    try:
                        message = messages.get_nowait()
                    except queue.Empty:
                        break
                    total += 1
                    triggers.extend(detector.feed(message))
                triggers.extend(detector.advance(now))
                if (
                    mark_after is not None
                    and not marked
                    and recorder.started_at
                    and now - recorder.started_at >= mark_after
                ):
                    triggers.append({"timestamp": now, "manual": True, "chat_examples": [], "ratio": 0})
                    marked = True
                for trigger in triggers:
                    if not recorder.started_at or not segments:
                        store.event("trigger_without_video", {"channel": name})
                        continue
                    capacity = intake_status(store, cap, len(pending))
                    if capacity["intake_state"] != "accepting":
                        store.event("queue_full", {"channel": name, **capacity})
                        continue
                    center = (
                        recorder.media_time(trigger["timestamp"], segments) - cap["chat_video_offset_seconds"]
                    )
                    start, end = (
                        max(segments[0]["start"], center - cap["pre_seconds"]),
                        center + cap["post_seconds"],
                    )
                    if any(
                        min(end, p["end"]) - max(start, p["start"]) > 0.6 * (end - start) for p in pending
                    ):
                        continue
                    pending.append({"start": start, "end": end, "center": center, "trigger": trigger})
                    store.event(
                        "spike", {"channel": name, "ratio": trigger.get("ratio"), "start": start, "end": end}
                    )
                for item in pending[:]:
                    if not segments or segments[-1]["end"] < item["end"]:
                        continue
                    save_window(item, segments)
                recorder.prune(segments, min((p["start"] for p in pending), default=None))
                if now - last_health >= 2:
                    capacity = intake_status(store, cap, len(pending))
                    if not segments:
                        capacity["intake_state"] = "waiting_for_video"
                    store.health(
                        name,
                        capture_state="recording" if segments else "waiting for video",
                        recorded_seconds=segments[-1]["end"] if segments else 0,
                        chat_messages=total,
                        pending_windows=len(pending),
                        **capacity,
                    )
                    last_health = now
                if not recorder.alive():
                    raise RelayError("Stream ended or capture failed; see the session capture log")
                stop.wait(0.5)
        except Exception as exc:  # noqa: BLE001 -- each channel is a supervised long-running boundary
            store.health(
                name, capture_state="reconnecting" if not source else "ended", capture_error=str(exc)[:300]
            )
            store.event(
                "capture_interrupted",
                {"channel": name, "pending_windows": len(pending), "reason": str(exc)[:300]},
            )
        finally:
            recorder.stop()
            segments = recorder.segments()
            for item in pending[:]:
                # A sealed partial post-roll can still contain a complete story.
                # Tell the editor it is truncated; never pretend missing footage exists.
                if (
                    segments
                    and segments[-1]["end"] >= item["center"] + min(cap["post_seconds"], 5)
                    and free_space_ok(cfg)
                ):
                    item["end"] = min(item["end"], segments[-1]["end"])
                    try:
                        save_window(item, segments, truncated=True)
                    except (RelayError, OSError) as exc:
                        store.event("partial_window_failed", {"channel": name, "reason": str(exc)[:200]})
            if pending:
                store.event("incomplete_windows_discarded", {"channel": name, "count": len(pending)})
            recorder.release_buffer()
            if recorders is not None:
                recorders.discard(recorder)
        if source:
            break
        stop.wait(cap["reconnect_seconds"])
    store.health(name, capture_state="stopped", intake_state="stopped")


def monitor(cfg, stop, *, duration=None, mark_after=None):
    store = Store(cfg)
    channels = [c for c in cfg["channels"] if c.get("enabled", True)]
    if not channels:
        raise RelayError("Enable at least one channel in config.toml")
    if len(channels) > cfg["capture"]["max_channels"]:
        raise RelayError("Enabled channels exceed capture.max_channels")
    queues = {c["name"]: queue.Queue(maxsize=200000) for c in channels}

    def callback(name, message):
        try:
            queues[name].put_nowait(message)
        except queue.Full:
            store.health(name, chat_error="Message queue full; some messages were dropped")

    threads, recorders = [], RecorderGroup()
    with ExitStack() as ownership:
        # Disjoint channel partitions can run on different machines. A second
        # recorder can never silently duplicate an already-owned channel.
        guards = {
            name: ownership.enter_context(store.lock("capture:" + name, seconds=90))
            for name in sorted(queues)
        }
        try:
            for transport in ("anonymous-irc", "eventsub"):
                names = [c["name"] for c in channels if c.get("chat", "eventsub") == transport]
                if not names:
                    continue
                target = anonymous_chat if transport == "anonymous-irc" else eventsub_chat
                args = (
                    (names, callback, stop, store.health)
                    if transport == "anonymous-irc"
                    else (cfg, names, callback, stop, store.health)
                )
                thread = threading.Thread(target=target, args=args, daemon=True)
                thread.start()
                threads.append(thread)
            for c in channels:
                thread = threading.Thread(
                    target=capture_channel,
                    args=(cfg, c, queues[c["name"]], stop, store),
                    kwargs={"mark_after": mark_after, "guard": guards[c["name"]], "recorders": recorders},
                )
                thread.start()
                threads.append(thread)
            deadline = time.monotonic() + duration if duration is not None else None
            while not stop.wait(0.5):
                if (deadline is not None and time.monotonic() >= deadline) or any(
                    g.lost.is_set() for g in guards.values()
                ):
                    break
        finally:
            stop.set()
            try:
                recorders.stop()
            finally:
                # Keep renewing ownership while a bounded extraction finishes.
                # Never hand this channel to another process while an old thread
                # can still save a window or mutate its recording directory.
                for thread in threads:
                    while thread.is_alive():
                        thread.join(timeout=1)
    return store.snapshot()


def monitor_config(cfg, stop, *, duration=None):
    """Apply saved capture settings by gracefully restarting the owned partition."""
    deadline = time.monotonic() + duration if duration is not None else None
    while not stop.is_set() and (deadline is None or time.monotonic() < deadline):
        cfg = load(cfg["config_path"])
        if not service_enabled(cfg, "capture") or not any(channel["enabled"] for channel in cfg["channels"]):
            stop.wait(2)
            continue
        partition_stop = threading.Event()
        errors = []

        def run_partition(current=cfg, stopping=partition_stop, failures=errors):
            try:
                monitor(current, stopping)
            except Exception as exc:  # noqa: BLE001 -- pass the failure to the supervisor
                failures.append(exc)

        thread = threading.Thread(target=run_partition, daemon=True)
        thread.start()
        try:
            while thread.is_alive() and not stop.wait(2):
                if deadline is not None and time.monotonic() >= deadline:
                    break
                current = load(cfg["config_path"])
                if not service_enabled(current, "capture"):
                    break
                if any(current[key] != cfg[key] for key in ("channels", "capture", "detector")):
                    break
        finally:
            partition_stop.set()
            thread.join()
        if errors:
            raise errors[0]
