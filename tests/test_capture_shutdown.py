"""Owned process cleanup while the recorder thread is busy extracting a clip."""

import copy
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from reelrelay.capture import Recorder, monitor
from reelrelay.config import DEFAULTS
from reelrelay.store import Store
from reelrelay.util import RelayError


class CaptureShutdownTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.cfg["channels"] = [{"name": "fixture", "enabled": True, "chat": "anonymous-irc"}]
        self.children = []

    def tearDown(self):
        for child in self.children:
            if child.poll() is None:
                child.send_signal(signal.SIGKILL)
            child.wait(timeout=5)
        self.temp.cleanup()

    def spawn_owned_child(self):
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.children.append(child)
        return child

    def test_shutdown_stops_children_before_extraction_and_retains_channel_ownership(self):
        extracting, finish_extract, stopping = (threading.Event() for _ in range(3))
        errors = []
        test = self

        class SyntheticRecorder(Recorder):
            def _start(self):
                self.started_at = time.time()
                self.segmenter = test.spawn_owned_child()

            def segments(self):
                return [
                    {
                        "path": str(self.folder / "fixture.ts"),
                        "start": 0,
                        "end": 100,
                        "closed_at": time.time(),
                    }
                ]

            def media_time(self, *_):
                return 10

        def slow_extract(segments, start, end, output, root):
            extracting.set()
            if not finish_extract.wait(5):
                raise RelayError("Test extraction was not released")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"synthetic retained clip")
            return start, end

        def run_monitor():
            try:
                monitor(self.cfg, stopping, mark_after=0)
            except Exception as exc:  # noqa: BLE001 -- report worker-thread failures to the test assertion
                errors.append(exc)

        with (
            patch("reelrelay.capture.Recorder", SyntheticRecorder),
            patch(
                "reelrelay.capture.anonymous_chat",
                side_effect=lambda channels, callback, stop, health: stop.wait(),
            ),
            patch("reelrelay.capture.media.extract", side_effect=slow_extract),
        ):
            thread = threading.Thread(target=run_monitor)
            thread.start()
            try:
                self.assertTrue(extracting.wait(5))
                stopping.set()
                self.children[0].wait(timeout=3)
                self.assertTrue(thread.is_alive(), "Ownership ended while extraction was still mutating")
                with self.assertRaises(RelayError), Store(self.cfg).lock("capture:fixture"):
                    self.fail("A new recorder claimed the still-running extraction's channel")
                finish_extract.set()
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertIsNotNone(self.children[0].poll())
                with Store(self.cfg).lock("capture:fixture"):
                    pass
            finally:
                stopping.set()
                finish_extract.set()
                thread.join(timeout=6)

    def test_stop_cannot_race_past_a_starting_recorder(self):
        recorder = Recorder(self.cfg, "fixture")
        entering, finish_start, stopped = (threading.Event() for _ in range(3))

        def delayed_start():
            entering.set()
            if not finish_start.wait(5):
                raise RelayError("Test startup was not released")
            recorder.segmenter = self.spawn_owned_child()

        def stop_recorder():
            recorder.stop()
            stopped.set()

        with patch.object(recorder, "_start", side_effect=delayed_start):
            starter = threading.Thread(target=recorder.start)
            closer = threading.Thread(target=stop_recorder)
            starter.start()
            try:
                self.assertTrue(entering.wait(5))
                closer.start()
                self.assertFalse(stopped.wait(0.1))
                finish_start.set()
                starter.join(timeout=3)
                closer.join(timeout=3)
                self.assertTrue(stopped.is_set())
                self.assertIsNotNone(self.children[0].poll())
                with self.assertRaises(RelayError):
                    recorder.start()
            finally:
                finish_start.set()
                starter.join(timeout=5)
                if closer.ident:
                    closer.join(timeout=5)
