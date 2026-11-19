"""Opt-in real FFmpeg tests, with generated fixture media only."""

import copy
import os
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from reelrelay import media
from reelrelay.capture import Recorder, capture_channel
from reelrelay.config import DEFAULTS
from reelrelay.store import Store
from reelrelay.util import RelayError


@unittest.skipUnless(
    os.environ.get("REEL_RELAY_MEDIA_TESTS") == "1", "Opt in to local FFmpeg integration tests"
)
class MediaIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.cfg["capture"].update(segment_seconds=2, retention_seconds=60, pre_seconds=3, post_seconds=2)
        self.source = self.root / "sources/fixture.mp4"
        self.source.parent.mkdir()
        media.run(
            [
                media.binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=320x180:rate=24:duration=80",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=80",
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
                "80",
                self.source,
            ],
            self.root,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_dense_inspection_decodes_a_bounded_source_window(self):
        images = media.detail_contact_sheets(
            self.source,
            self.root / "evidence/detail",
            self.root,
            [{"start": 5, "end": 8, "reason": "Fixture motion"}],
        )
        self.assertEqual(len(images), 1)
        self.assertGreater(images[0].stat().st_size, 1000)
        with self.assertRaises(RelayError):
            media.detail_contact_sheets(
                self.source, self.root / "evidence/too-long", self.root, [{"start": 5, "end": 30}]
            )

    def test_segment_pruning_extraction_and_source_preservation(self):
        recorder = Recorder(self.cfg, "fixture", source=self.source, realtime=False)
        try:
            recorder.start()
            recorder.segmenter.wait(timeout=30)
            recorder.stop()
            segments = recorder.segments()
            self.assertGreaterEqual(segments[-1]["end"], 79)
            recorder.prune(segments)
            remaining = recorder.segments()
            self.assertLessEqual(len(remaining), 32)
            self.assertLessEqual(len(recorder.seen), 32)
            output = self.root / "sources" / "candidate.mp4"
            start, end = media.extract(remaining, 65, 73, output, self.root)
            self.assertEqual((start, end), (65, 73))
            self.assertAlmostEqual(media.probe(output, self.root)["duration"], 8, delta=0.2)
            media.run(
                [media.binary("ffmpeg"), "-v", "error", "-nostdin", "-i", output, "-f", "null", "-"],
                self.root,
            )
            recorder.release_buffer()
            self.assertEqual(list(recorder.folder.glob("segment-*.ts")), [])
            self.assertTrue(output.exists())
            self.assertTrue(self.source.exists())
        finally:
            recorder.stop()

    def test_live_replay_saves_marked_window_and_releases_buffer_on_stop(self):
        store, stopping = Store(self.cfg), threading.Event()
        thread = threading.Thread(
            target=capture_channel,
            args=(self.cfg, {"name": "fixture"}, queue.Queue(), stopping, store),
            kwargs={"source": self.source, "mark_after": 6},
        )
        thread.start()
        try:
            deadline = time.monotonic() + 25
            while not store.list() and time.monotonic() < deadline:
                time.sleep(0.2)
            self.assertTrue(store.list(), "Expected a real extracted clip after the manual test marker")
            item = store.list()[0]
            self.assertTrue(item["metadata"]["trigger"]["manual"])
            self.assertGreater(media.probe(item["source_path"], self.root)["duration"], 3)
        finally:
            stopping.set()
            thread.join(timeout=20)
        self.assertFalse(thread.is_alive())
        self.assertEqual(list((self.root / "recordings").rglob("segment-*.ts")), [])
        self.assertTrue(self.source.exists())

    def test_low_disk_stops_before_starting_media_process(self):
        recorder = Recorder(self.cfg, "fixture", source=self.source)
        with patch("reelrelay.capture.free_space_ok", return_value=False), self.assertRaises(RelayError):
            recorder.start()
        self.assertIsNone(recorder.segmenter)


if __name__ == "__main__":
    unittest.main()
