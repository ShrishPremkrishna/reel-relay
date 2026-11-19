from __future__ import annotations

import copy
import json
from pathlib import Path

from . import media
from .capture import Recorder
from .detector import ChatMessage, SpikeDetector
from .store import Store
from .util import RelayError, read_json, sha256, write_json
from .worker import apply_decision, step


def demo(cfg, *, render=False):
    root = Path(cfg["data_dir"])
    folder = root / "demo"
    folder.mkdir(exist_ok=True)
    source = folder / "synthetic-stream.mp4"
    if source.exists():
        try:
            media.probe(source, root)
        except (RelayError, ValueError):
            source.unlink()  # Only this generated fixture, never imported footage.
    if not source.exists():
        speech = folder / "speech.aiff"
        if not speech.exists() and Path("/usr/bin/say").exists():
            media.run(
                [
                    "/usr/bin/say",
                    "-v",
                    "Samantha",
                    "-o",
                    speech,
                    "I have a brilliant plan. Take the shortcut. What could possibly go wrong? That was not the shortcut.",
                ],
                root,
            )
        speech_duration = 0
        if speech.exists():
            info = json.loads(
                media.run(
                    [media.binary("ffprobe"), "-v", "error", "-show_format", "-of", "json", speech], root, 30
                )
            )
            speech_duration = float(info.get("format", {}).get("duration", 0))
        if speech_duration > 1:
            audio = ["-i", str(speech)]
            af = "[1:a]adelay=9000|9000,apad,atrim=0:24[a]"
        else:
            audio = ["-f", "lavfi", "-i", "sine=frequency=440:duration=24"]
            af = "[1:a]volume=0.03[a]"
        graph = (
            "[0:v]drawtext=text='REEL RELAY / SYNTHETIC TEST':fontcolor=white:fontsize=28:x=30:y=25,"
            "drawbox=x=480:y=190:w=80:h=100:color=red:t=fill:enable='gte(t,14)',"
            "drawtext=text='THE SHORTCUT':fontcolor=white:fontsize=40:x=40:y=160:enable='between(t,9,14)',"
            "drawtext=text='WRONG TURN':fontcolor=yellow:fontsize=48:x=40:y=160:enable='gte(t,14)'[v];" + af
        )
        media.run(
            [
                media.binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-n",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x152c37:s=640x360:r=24:d=24",
                *audio,
                "-filter_complex",
                graph,
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-g",
                "48",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-t",
                "24",
                "-movflags",
                "+faststart",
                source,
            ],
            root,
        )
    local = copy.deepcopy(cfg)
    local["capture"]["segment_seconds"] = 2
    local["detector"].update(
        window_seconds=2,
        warmup_windows=6,
        baseline_windows=10,
        min_messages=12,
        min_users=6,
        cooldown_seconds=10,
    )
    recorder = Recorder(local, "synthetic", source=source, realtime=False)
    try:
        recorder.start()
        recorder.segmenter.wait(timeout=60)
        segments = recorder.segments()
    finally:
        recorder.stop()
    detector, triggers = SpikeDetector(local["detector"]), []
    replay = []
    for second in range(24):
        users = 20 if 12 <= second < 14 else 2
        for user in range(users):
            message = ChatMessage(
                second + 0.1 + user * 0.01,
                f"viewer{user}",
                "wrong turn!" if users > 2 else "hello",
                f"{second}-{user}",
            )
            replay.append(
                {
                    "timestamp": message.timestamp,
                    "user": message.user,
                    "text": message.text,
                    "id": message.message_id,
                }
            )
            triggers.extend(detector.feed(message))
    triggers.extend(detector.advance(24))
    if not triggers:
        raise RuntimeError("Synthetic spike was not detected")
    trigger = triggers[0]
    candidate_file = folder / ("candidate-" + recorder.session_id + ".mp4")
    start, end = media.extract(
        segments, trigger["timestamp"] - 10, trigger["timestamp"] + 8, candidate_file, root
    )
    recorder.release_buffer()
    store = Store(cfg)
    cid = store.add(
        "synthetic",
        candidate_file,
        sha256(candidate_file),
        session_id=recorder.session_id,
        start=start,
        end=end,
        metadata={"trigger": trigger, "fixture": True},
        is_test=True,
        attribution="Synthetic fixture. No streamer footage. Never publish.",
    )
    write_json(folder / "chat-replay.json", replay)
    report = {
        "candidate_id": cid,
        "trigger_at": trigger["timestamp"],
        "spike_ratio": trigger["ratio"],
        "captured_start": start,
        "captured_end": end,
        "segments": len(segments),
        "source": str(source),
        "candidate": str(candidate_file),
        "is_test": True,
    }
    if render:
        local["agent"]["provider"] = "manual"
        item = store.get(cid)
        if item["status"] == "captured":
            step(local, cid=cid)
        item = store.get(cid)
        if item["status"] == "needs_agent":
            evidence = read_json(item["evidence_path"])
            begin, finish = max(0, 8.5 - start), min(evidence["duration"], 20 - start)
            decision = {
                "verdict": "accept",
                "score": 8,
                "confidence": 1,
                "reason": "Deterministic synthetic integration fixture, not an AI entertainment assessment.",
                "hook": "Synthetic pipeline test",
                "caption": "Synthetic integration test",
                "flags": [],
                "layout": "contain",
                "facecam": None,
                "keep": [
                    {
                        "start": begin,
                        "end": finish,
                        "reason": "Keep the test narration and visible change",
                        "focus_x": 0.5,
                        "focus_y": 0.5,
                        "zoom": 1,
                    }
                ],
                "sfx": [],
                "overlays": [],
            }
            apply_decision(local, cid, decision)
        if store.get(cid)["status"] == "selected":
            step(local, cid=cid)
        report["output"] = store.get(cid)["output_path"]
        report["state"] = store.get(cid)["status"]
        evidence_path = store.get(cid)["evidence_path"]
        if evidence_path:
            transcript = read_json(evidence_path).get("transcript", {})
            report["transcribed_words"] = len(transcript.get("words", []))
            if not report["transcribed_words"]:
                report["note"] = (
                    "No speech was transcribed. This run verifies media rendering only; use a spoken fixture to verify subtitles."
                )
    write_json(folder / "report.json", report)
    return report
