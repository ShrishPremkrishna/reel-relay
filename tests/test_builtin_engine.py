"""Standalone engine contracts, plus opt-in real offline rendering integration."""

import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelrelay import media
from reelrelay.config import DEFAULTS
from reelrelay.engine import BuiltinStudio
from reelrelay.engine.backend import project_lock
from reelrelay.engine.captions import (
    ass_text,
    caption_cues,
    corrected_words,
    normalize_transcript,
    timeline_words,
    write_subtitles,
)
from reelrelay.engine.render import encode, region
from reelrelay.util import RelayError, TransientError, read_json, sha256, write_json


def decision(**changes):
    value = {
        "verdict": "accept",
        "score": 8.0,
        "confidence": 0.9,
        "reason": "Synthetic renderer verification only",
        "hook": "Fixture only",
        "caption": "Never publish this test",
        "flags": [],
        "layout": "contain",
        "facecam": None,
        "keep": [
            {"start": 0.0, "end": 6.0, "reason": "Fixture", "focus_x": 0.5, "focus_y": 0.5, "zoom": 1.0}
        ],
        "sfx": [],
        "overlays": [],
    }
    value.update(changes)
    return value


class CaptionContracts(unittest.TestCase):
    def test_source_hash_and_word_timing_are_enforced(self):
        for words in (
            [{"start": float("nan"), "end": 1, "text": "bad"}],
            [{"start": True, "end": 1, "text": "bad"}],
            [{"start": 2, "end": 1, "text": "bad"}],
            [{"start": 1, "end": 1.2, "text": "later"}, {"start": 0, "end": 0.2, "text": "earlier"}],
        ):
            with self.assertRaises(RelayError):
                normalize_transcript({"words": words}, 5, "source")
        with self.assertRaises(RelayError):
            normalize_transcript({"words": [], "source_sha256": "different"}, 5, "source")

    def test_corrections_and_reordered_cuts_preserve_source_transcript(self):
        transcript = {
            "words": [
                {"start": 0.2, "end": 0.5, "text": "Wrong"},
                {"start": 3.2, "end": 3.6, "text": "Later"},
            ]
        }
        corrected = corrected_words(
            transcript, [{"start": 0.1, "end": 0.7, "text": "Right name", "reason": "Checked"}], 5
        )
        self.assertEqual(transcript["words"][0]["text"], "Wrong")
        output = timeline_words(corrected, [{"start": 3, "end": 4}, {"start": 0, "end": 1}])
        self.assertEqual([word["text"] for word in output], ["Later", "Right", "name"])
        self.assertAlmostEqual(output[0]["start"], 0.2)
        self.assertAlmostEqual(output[1]["start"], 1.1)
        with self.assertRaises(RelayError):
            corrected_words(
                transcript, [{"start": 0, "end": 1, "text": "a"}, {"start": 0.5, "end": 1.5, "text": "b"}], 5
            )

    def test_caption_groups_preserve_comic_pauses_and_escape_overrides(self):
        words = [{"start": 0, "end": 0.2, "text": "Wait"}, {"start": 1.5, "end": 1.8, "text": r"{\pos(0,0)}"}]
        self.assertEqual(len(caption_cues(words)), 2)
        escaped = ass_text(words[1]["text"])
        self.assertNotIn("{", escaped)
        self.assertNotIn("\\", escaped)
        self.assertIn("pos", escaped)

    def test_framing_rejects_nonfinite_or_outside_rectangles(self):
        for rect in (
            {"x": 0.9, "y": 0, "width": 0.2, "height": 1},
            {"x": 0, "y": float("inf"), "width": 1, "height": 1},
        ):
            with self.assertRaises(RelayError):
                region(rect)


class EngineArtifactContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.engine = BuiltinStudio(self.cfg)
        self.engine.ensure()

    def tearDown(self):
        self.temporary.cleanup()

    def test_catalog_rejects_paths_outside_artifacts_and_hash_changes(self):
        path = self.root / "engine/assets/catalog.json"
        write_json(path, [{"path": "../../../../outside.wav", "kind": "sfx"}])
        with self.assertRaises(RelayError):
            self.engine.catalog()
        asset = path.parent / "tone.wav"
        asset.write_bytes(b"original")
        write_json(path, [{"path": "tone.wav", "kind": "sfx", "duration": 1}])
        rows = self.engine.catalog()
        self.assertEqual(rows[0]["sha256"], sha256(asset))
        write_json(path, [{"path": "tone.wav", "kind": "sfx", "sha256": rows[0]["sha256"], "duration": 1}])
        asset.write_bytes(b"changed")
        with self.assertRaises(RelayError):
            self.engine.catalog()

    def test_same_project_cannot_be_mutated_by_simultaneous_operations(self):
        project = self.root / "project"
        with project_lock(project), self.assertRaises(TransientError), project_lock(project):
            self.fail("Concurrent operation acquired the same project")
        with project_lock(project):
            self.assertTrue(project.is_dir())

    def test_runtime_readiness_distinguishes_hidden_home_env_from_data_venv(self):
        self.engine.python = Path.home() / "unrelated-relay-asr/bin/python"
        self.assertFalse(self.engine._runtime_location_allowed())
        interpreter = self.root / "engine/asr-env/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(Path(sys.executable).resolve())
        self.engine.python = interpreter
        self.assertTrue(self.engine.readiness()["transcription_runtime"])

    def test_catalog_duration_must_be_finite_positive_number_or_unknown(self):
        path = self.root / "engine/assets/catalog.json"
        (path.parent / "tone.wav").write_bytes(b"catalog fixture")
        for duration in ("0:05", float("inf"), True, -1, 0):
            with self.subTest(duration=duration):
                path.write_text(json.dumps([{"path": "tone.wav", "kind": "sfx", "duration": duration}]))
                with self.assertRaises(RelayError):
                    self.engine.catalog()
        write_json(path, [{"path": "tone.wav", "kind": "sfx", "duration": None}])
        self.assertIsNone(self.engine.catalog()[0]["duration"])

    def test_effect_cannot_claim_longer_duration_than_decoded_media(self):
        plan = decision(
            sfx=[
                {
                    "asset_path": "tiny.wav",
                    "at": 1,
                    "duration": 0.5,
                    "gain_db": -12,
                    "reason": "Invalid exaggerated catalog duration",
                }
            ]
        )
        with self.assertRaisesRegex(RelayError, "decoded asset duration"):
            encode(
                self.root / "source.mp4",
                {"duration": 8, "width": 320, "height": 180, "has_audio": True},
                plan,
                {
                    "tiny.wav": {
                        "resolved_path": str(self.root / "tiny.wav"),
                        "kind": "sfx",
                        "duration": 0.1,
                        "has_audio": True,
                    }
                },
                self.root / "captions.ass",
                self.root / "output.mp4",
                self.root,
            )


@unittest.skipUnless(
    os.environ.get("REEL_RELAY_MEDIA_TESTS") == "1", "Opt in to standalone FFmpeg integration"
)
class StandaloneMediaIntegration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.engine = BuiltinStudio(self.cfg)
        self.source = self.root / "sources/source.mp4"
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
                "color=c=red:s=320x180:r=30:d=8,drawbox=x=160:y=0:w=160:h=180:color=blue:t=fill",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=8",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-t",
                "8",
                self.source,
            ],
            self.root,
        )
        self.candidate = {
            "id": "a" * 16,
            "channel": "synthetic",
            "source_path": str(self.source),
            "source_hash": sha256(self.source),
            "is_test": True,
            "metadata": {},
            "attribution": "Generated test fixture",
        }
        transcript = {
            "words": [
                {"start": 0.3, "end": 0.6, "text": "Fixture"},
                {"start": 1.3, "end": 1.7, "text": r"{\pos(0,0)}"},
                {"start": 4.1, "end": 4.8, "text": "ending."},
            ]
        }
        self.engine.import_transcript(self.candidate, transcript, "Synthetic fixture timings, not ASR")
        self.project, evidence = self.engine.prepare(self.candidate)
        self.candidate["evidence_path"] = str(evidence)
        self.candidate["decision"] = decision()

    def tearDown(self):
        self.temporary.cleanup()

    def test_standalone_export_is_complete_and_each_review_attempt_is_immutable(self):
        first_project, first, first_review = self.engine.render(self.candidate)
        manifest = read_json(first / "manifest.json")
        self.assertEqual(manifest["output_sha256"], sha256(first / "reel.mp4"))
        self.assertEqual(manifest["artifact_sha256"]["captions.ass"], sha256(first / "captions.ass"))
        self.assertEqual(
            manifest["artifact_sha256"]["contact-sheet.jpg"], sha256(first / "contact-sheet.jpg")
        )
        self.assertEqual(manifest["output"], {"width": 1080, "height": 1920, "fps": 30})
        self.assertEqual(read_json(first / "qa.json")["warnings"], [])
        original_packet = first_review.read_bytes()
        self.candidate["decision"]["hook"] = "Second immutable attempt"
        _, second, second_review = self.engine.render(self.candidate)
        self.assertNotEqual(first, second)
        self.assertNotEqual(first_review, second_review)
        self.assertEqual(first_review.read_bytes(), original_packet)
        self.assertEqual(sha256(first / "reel.mp4"), manifest["output_sha256"])
        self.assertFalse((first_project / "latest-export.json").exists())
        self.assertTrue((first / "poster.jpg").is_file())
        self.assertTrue((second / "contact-sheet.jpg").is_file())
        self.assertEqual(Path(read_json(second_review)["video_path"]), second / "reel.mp4")

    def test_frozen_editing_settings_control_real_render_and_caption_burn(self):
        preferences = {
            **DEFAULTS["editing"],
            "layout": "focus",
            "captions_enabled": False,
            "caption_words": 1,
        }
        self.candidate["metadata"]["editing_settings"] = preferences
        self.cfg["editing"].update(layout="contain", captions_enabled=True, caption_words=6)
        _, export, review = self.engine.render(self.candidate)
        manifest = read_json(export / "manifest.json")
        self.assertEqual(read_json(export / "editing.json"), preferences)
        self.assertEqual(manifest["editing_sha256"], sha256(export / "editing.json"))
        self.assertEqual(read_json(review)["editing"], preferences)
        self.assertEqual(read_json(export / "edit.json")["layout"], "focus")
        self.assertEqual(self.candidate["decision"]["layout"], "contain")
        captions = read_json(export / "captions.json")
        self.assertFalse(captions["enabled"])
        self.assertTrue(captions["cues"])
        self.assertTrue(all(len(cue["text"].split()) <= 1 for cue in captions["cues"]))
        self.assertNotIn("Dialogue: 0", (export / "captions.ass").read_text())
        self.assertIn("Dialogue: 1", (export / "captions.ass").read_text())
        self.assertIn("Fixture", (export / "captions.srt").read_text())
        self.assertEqual(read_json(export / "qa.json")["full_decode"], "passed")

    def test_custom_two_subject_panels_render_without_legacy_code(self):
        folder = self.root / "panels"
        folder.mkdir()
        write_subtitles(folder, [], "", 6, width=360, height=640)
        plan = decision(
            framing={
                "mode": "panels",
                "split": 0.5,
                "regions": [
                    {"x": 0, "y": 0, "width": 0.5, "height": 1},
                    {"x": 0.5, "y": 0, "width": 0.5, "height": 1},
                ],
            }
        )
        encode(
            self.source,
            {"duration": 8, "width": 320, "height": 180, "has_audio": True},
            plan,
            {},
            folder / "captions.ass",
            folder / "reel.mp4",
            self.root,
            width=360,
            height=640,
        )
        info = media.probe(folder / "reel.mp4", self.root)
        video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (360, 640))
        # Sample each panel's center pixel using FFmpeg itself inside the sandbox.
        top = folder / "top.rgb"
        bottom = folder / "bottom.rgb"
        for y, path in [(100, top), (500, bottom)]:
            media.run(
                [
                    media.binary("ffmpeg"),
                    "-v",
                    "error",
                    "-nostdin",
                    "-i",
                    folder / "reel.mp4",
                    "-vf",
                    f"crop=2:2:180:{y}",
                    "-frames:v",
                    "1",
                    "-pix_fmt",
                    "rgb24",
                    "-f",
                    "rawvideo",
                    path,
                ],
                self.root,
            )
        self.assertGreater(top.read_bytes()[0], top.read_bytes()[2] + 100)
        self.assertGreater(bottom.read_bytes()[2], bottom.read_bytes()[0] + 100)

    def test_failed_render_cannot_publish_or_replace_an_existing_export(self):
        _, first, first_review = self.engine.render(self.candidate)
        before = sha256(first / "reel.mp4")
        with (
            patch("reelrelay.engine.backend.encode", side_effect=RelayError("injected failure")),
            self.assertRaises(RelayError),
        ):
            self.engine.render(self.candidate)
        failures = list((self.project / "exports").glob("*/failure.json"))
        self.assertEqual(len(failures), 1)
        self.assertFalse(read_json(failures[0])["complete"])
        self.assertFalse((failures[0].parent / "manifest.json").exists())
        self.assertEqual(sha256(first / "reel.mp4"), before)
        self.assertTrue(first_review.is_file())

    def test_prepare_packets_and_asset_receipts_cannot_change_under_an_editor(self):
        old_packet = Path(self.candidate["evidence_path"])
        old_bytes = old_packet.read_bytes()
        _, later = self.engine.prepare(self.candidate)
        self.assertNotEqual(old_packet, later)
        self.assertEqual(old_packet.read_bytes(), old_bytes)
        self.source.write_bytes(b"changed source")
        with self.assertRaises(RelayError):
            self.engine.render(self.candidate)

    def test_catalog_effects_are_mixed_and_changes_after_selection_are_rejected(self):
        folder = self.root / "engine/assets"
        tone, visual = folder / "accent.wav", folder / "reaction.mp4"
        media.run(
            [
                media.binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1300:duration=1",
                tone,
            ],
            self.root,
        )
        media.run(
            [
                media.binary("ffmpeg"),
                "-v",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "color=c=green:s=96x96:r=30:d=1",
                "-c:v",
                "libx264",
                visual,
            ],
            self.root,
        )
        write_json(
            folder / "catalog.json",
            [
                {
                    "path": "accent.wav",
                    "kind": "sfx",
                    "duration": 1,
                    "rights": "Original fixture",
                    "attribution": "Generated tone",
                    "source_url": "",
                    "sha256": sha256(tone),
                },
                {
                    "path": "reaction.mp4",
                    "kind": "meme",
                    "duration": 1,
                    "rights": "Original fixture",
                    "attribution": "Generated visual",
                    "source_url": "",
                    "sha256": sha256(visual),
                },
            ],
        )
        _, evidence = self.engine.prepare(self.candidate)
        self.candidate["evidence_path"] = str(evidence)
        self.candidate["decision"] = decision(
            sfx=[
                {
                    "asset_path": "engine/assets/accent.wav",
                    "at": 1,
                    "duration": 0.5,
                    "gain_db": -12,
                    "reason": "Fixture accent",
                }
            ],
            overlays=[
                {
                    "asset_path": "engine/assets/reaction.mp4",
                    "at": 3,
                    "duration": 0.8,
                    "source_start": 0,
                    "width_fraction": 0.3,
                    "height_fraction": 0.3,
                    "x": 0.5,
                    "y": 0.5,
                    "reason": "Fixture reaction",
                }
            ],
        )

        def render_frozen_inputs(*args, **kwargs):
            for row in args[3].values():
                self.assertEqual(Path(row["resolved_path"]).parent.name, "inputs")
                self.assertEqual(sha256(Path(row["resolved_path"])), row["sha256"])
            # Simulate a library refresh after inputs were captured. Encoding
            # still succeeds because it consumes the immutable attempt copies.
            tone.write_bytes(b"Concurrent catalog refresh")
            return encode(*args, **kwargs)

        with patch("reelrelay.engine.backend.encode", side_effect=render_frozen_inputs):
            _, export, _ = self.engine.render(self.candidate)
        self.assertEqual(len(read_json(export / "assets.json")), 2)
        self.assertIn("Generated tone", (export / "credits.txt").read_text())
        self.assertEqual(read_json(export / "qa.json")["warnings"], [])
        tone.write_bytes(b"Changed after selection")
        with self.assertRaisesRegex(RelayError, "changed after editorial selection"):
            self.engine.render(self.candidate)

    def test_source_change_during_encode_leaves_no_complete_manifest(self):
        def changing_source(*args, **kwargs):
            result = encode(*args, **kwargs)
            self.source.write_bytes(b"Concurrent source mutation")
            return result

        with (
            patch("reelrelay.engine.backend.encode", side_effect=changing_source),
            self.assertRaisesRegex(RelayError, "Source changed during rendering"),
        ):
            self.engine.render(self.candidate)
        attempts = list((self.project / "exports").iterdir())
        self.assertEqual(len(attempts), 1)
        self.assertTrue((attempts[0] / "failure.json").is_file())
        self.assertFalse((attempts[0] / "manifest.json").exists())

    def test_subtitle_filter_paths_are_literal_even_with_parser_metacharacters(self):
        folder = self.root / "quotes' brackets[1]; comma, colon: slash\\"
        folder.mkdir()
        write_subtitles(folder, [], "Literal path", 6, width=360, height=640)
        encode(
            self.source,
            {"duration": 8, "width": 320, "height": 180, "has_audio": True},
            decision(),
            {},
            folder / "captions.ass",
            folder / "reel.mp4",
            self.root,
            width=360,
            height=640,
        )
        self.assertTrue((folder / "reel.mp4").is_file())

    def test_decoder_cannot_read_private_files_or_unrelated_home_media(self):
        private = self.root / ".private/hidden.mp4"
        private.parent.mkdir(exist_ok=True)
        shutil.copy2(self.source, private)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / "runtime") as other:
            outside = Path(other) / "unrelated.mp4"
            shutil.copy2(self.source, outside)
            for forbidden in (private, outside):
                with self.assertRaises(RelayError):
                    media.run([media.binary("ffprobe"), "-v", "error", "-show_format", forbidden], self.root)


if __name__ == "__main__":
    unittest.main()
