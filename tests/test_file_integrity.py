"""Interrupted encodes must not masquerade as complete reusable media."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelrelay import media
from reelrelay.util import RelayError


class MediaIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / "candidate.mp4"

    def tearDown(self):
        self.temp.cleanup()

    def test_interrupted_encode_leaves_no_finished_file(self):
        def fail(args, *_):
            Path(args[-1]).write_bytes(b"partial encoding")
            raise RelayError("Encoder stopped")

        with patch.object(media, "run", side_effect=fail), self.assertRaises(RelayError):
            media.render_atomic(["ffmpeg"], self.output, self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_truncated_successful_process_is_not_accepted(self):
        def encode(args, *_):
            Path(args[-1]).write_bytes(b"valid but truncated video")

        info = {"duration": 2, "streams": [{"codec_type": "video"}]}
        with (
            patch.object(media, "run", side_effect=encode),
            patch.object(media, "probe", return_value=info),
            self.assertRaises(RelayError),
        ):
            media.render_atomic(["ffmpeg"], self.output, self.root, expected_duration=10)
        self.assertFalse(self.output.exists())

    def test_concurrent_result_is_never_overwritten(self):
        def encode(args, *_):
            Path(args[-1]).write_bytes(b"this process")
            self.output.write_bytes(b"other process completed first")

        info = {"duration": 10, "streams": [{"codec_type": "video"}]}
        with (
            patch.object(media, "run", side_effect=encode),
            patch.object(media, "probe", return_value=info),
            self.assertRaises(FileExistsError),
        ):
            media.render_atomic(["ffmpeg"], self.output, self.root)
        self.assertEqual(self.output.read_bytes(), b"other process completed first")
        self.assertEqual(list(self.root.iterdir()), [self.output])
