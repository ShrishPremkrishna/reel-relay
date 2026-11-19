"""Rendered audio is checked independently, with measured rather than claimed confidence."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelrelay.audio_review import inspect_rendered_speech, normalized_words, ordered_alignment
from reelrelay.config import DEFAULTS
from reelrelay.engine import BuiltinStudio
from reelrelay.perception import read_verified
from reelrelay.util import RelayError, read_json, sha256, write_json


class Alignment(unittest.TestCase):
    def test_normalization_is_explicit_and_does_not_guess_words(self):
        self.assertEqual(
            normalized_words("TWO number 45s—with CHEESE!"),
            normalized_words("two number forty-fives with cheese"),
        )
        self.assertEqual(normalized_words("DON’T, stop."), normalized_words("don't stop"))
        self.assertNotEqual(normalized_words("I'll go"), normalized_words("I will go"))
        self.assertNotEqual(normalized_words("nine"), normalized_words("nines"))

    def test_order_and_repetitions_are_not_bag_of_words_matches(self):
        self.assertEqual(ordered_alignment(["a", "b", "a"], ["b", "a", "a"]), [[0, 1], [2, 2]])
        self.assertEqual(ordered_alignment(["one", "one", "one"], ["one"]), [[2, 0]])
        self.assertEqual(ordered_alignment(["a"], []), [])


class RenderedSpeech(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.cfg["media"]["transcription_model"] = "engine/models/test-model"
        self.export = self.root / "engine/projects/fixture/exports/unique-attempt"
        self.export.mkdir(parents=True)
        self.source = self.export / "reel.mp4"
        self.source.write_bytes(b"independent rendered audio fixture")
        self.candidate = {"output_path": str(self.source), "output_hash": sha256(self.source)}
        self.caption_text = "Did you get all that"
        self.recovered = self.caption_text
        self.qa = {
            "full_decode": "passed",
            "expected_duration": 5,
            "warnings": [],
            "audio_silent": False,
            "loudness": {"input_i": "-16", "input_tp": "-2"},
        }
        self.write_export()

    def tearDown(self):
        self.temporary.cleanup()

    def write_export(self):
        write_json(
            self.export / "captions.json", {"cues": [{"start": 0, "end": 4, "text": self.caption_text}]}
        )
        write_json(self.export / "qa.json", self.qa)
        write_json(
            self.export / "manifest.json",
            {
                "complete": True,
                "duration": 5,
                "output_sha256": self.candidate["output_hash"],
                "artifact_sha256": {
                    name: sha256(self.export / name) for name in ("captions.json", "qa.json")
                },
            },
        )

    def captionless_export(self, *, words=None, corrections=None, keep=None, verdict="accept"):
        words, corrections = words or [], corrections or []
        write_json(self.export / "captions.json", {"cues": [], "corrections": corrections})
        write_json(
            self.export / "edit.json",
            {
                "verdict": verdict,
                "keep": keep if keep is not None else [{"start": 0, "end": 5}],
                "caption_corrections": corrections,
            },
        )
        write_json(self.export / "transcript.json", {"words": words, "source_sha256": "f" * 64})
        manifest = read_json(self.export / "manifest.json")
        manifest.update(
            source_sha256="f" * 64,
            timeline_sha256=sha256(self.export / "edit.json"),
            transcript_sha256=sha256(self.export / "transcript.json"),
        )
        manifest["artifact_sha256"].update(
            {name: sha256(self.export / name) for name in ("captions.json", "edit.json", "transcript.json")}
        )
        write_json(self.export / "manifest.json", manifest)

    def transcribe(self, audio, folder, duration, digest):
        self.assertEqual(digest, self.candidate["output_hash"])
        self.assertEqual(duration, 5)
        self.assertTrue(Path(audio).is_file())
        transcript = {
            "words": [
                {"start": index * 0.2, "end": (index + 1) * 0.2, "text": word}
                for index, word in enumerate(self.recovered.split())
            ],
            "segments": [{"start": 0, "end": 4, "text": self.recovered}],
            "backend": "fixture-asr",
        }
        raw = Path(folder) / "asr-raw-fixture.json"
        write_json(raw, transcript)
        return transcript, {
            "model": "test-model",
            "raw_path": str(raw),
            "raw_sha256": sha256(raw),
            "local_files_only": True,
        }

    def inspect(self, transcribe=None):
        with (
            patch(
                "reelrelay.audio_review.media.probe",
                return_value={"duration": 5, "streams": [{"codec_type": "audio"}]},
            ),
            patch("reelrelay.audio_review.media.binary", return_value="/usr/bin/ffmpeg"),
            patch(
                "reelrelay.audio_review.media.run",
                side_effect=lambda args, root: Path(args[-1]).write_bytes(b"canonical audio fixture"),
            ),
            patch(
                "reelrelay.audio_review.BuiltinStudio.transcribe_audio",
                side_effect=transcribe or self.transcribe,
            ) as asr,
        ):
            return inspect_rendered_speech(self.cfg, self.candidate), asr

    def test_good_match_binds_exact_export_and_preserves_export_files(self):
        before = {path.name: sha256(path) for path in self.export.iterdir()}
        path, asr = self.inspect()
        packet = read_verified(path, self.candidate["output_hash"], self.root)
        self.assertTrue(packet["passed"])
        self.assertEqual(packet["provider"], "local-asr")
        self.assertEqual(packet["model"], "test-model")
        self.assertEqual(packet["purpose"], "preview")
        self.assertEqual(packet["modalities"], ["audio"])
        self.assertEqual(packet["assessment"]["confidence"], 1)
        self.assertEqual(packet["metrics"]["caption_word_coverage"], 1)
        self.assertEqual(packet["assessment"]["audio_issues"], [])
        self.assertEqual(before, {path.name: sha256(path) for path in self.export.iterdir()})
        self.assertFalse(path.is_relative_to(self.export))
        self.assertEqual(asr.call_count, 1)
        for name, digest in packet["inputs"][0]["artifact_sha256"].items():
            self.assertEqual(sha256(path.parent / name), digest)

    def test_mismatch_produces_explicit_issues_and_measured_coverage(self):
        self.recovered = "Did that happen"
        path, _ = self.inspect()
        packet = read_json(path)
        self.assertTrue(packet["complete"])
        self.assertFalse(packet["passed"])
        self.assertEqual(packet["metrics"]["matched_word_count"], 2)
        self.assertEqual(packet["assessment"]["confidence"], 0.4)
        self.assertIn("at least", packet["assessment"]["audio_issues"][0])

    def test_fewer_than_three_expected_words_never_passes(self):
        self.caption_text = self.recovered = "Oh no"
        self.write_export()
        path, _ = self.inspect()
        packet = read_json(path)
        self.assertEqual(packet["assessment"]["confidence"], 1)
        self.assertFalse(packet["passed"])
        self.assertIn("three", packet["assessment"]["audio_issues"][0])

    def test_captionless_visual_edit_gets_only_technical_checks_without_asr(self):
        self.captionless_export(words=[{"start": 8, "end": 9, "text": "Outside the retained edit"}])
        before = {path.name: sha256(path) for path in self.export.iterdir()}
        path, asr = self.inspect()
        asr.assert_not_called()
        packet = read_verified(path, self.candidate["output_hash"], self.root)
        self.assertEqual(packet["provider"], "local-technical")
        self.assertEqual(packet["model"], "ffmpeg")
        self.assertEqual(packet["check_type"], "technical-audio")
        self.assertTrue(packet["complete"] and packet["passed"])
        self.assertTrue(all(packet["checks"].values()))
        self.assertFalse(packet["speech_assessed"])
        self.assertEqual(packet["assessment"]["confidence"], 0)
        self.assertEqual(packet["assessment"]["speech"], [])
        self.assertEqual(packet["assessment"]["events"], [])
        self.assertIsNone(packet["metrics"]["caption_word_coverage"])
        self.assertIsNone(packet["metrics"]["recovered_word_count"])
        self.assertFalse((path.parent / "rendered-audio.wav").exists())
        self.assertFalse(list(path.parent.glob("asr-raw*")))
        self.assertEqual(before, {path.name: sha256(path) for path in self.export.iterdir()})

    def test_empty_cues_cannot_hide_retained_transcript_or_corrected_speech(self):
        for changes in (
            {"words": [{"start": 1, "end": 2, "text": "Expected speech"}]},
            {"corrections": [{"start": 1, "end": 2, "text": "Confirmed speech"}]},
        ):
            self.captionless_export(**changes)
            with (
                self.subTest(changes=changes),
                self.assertRaisesRegex(RelayError, "conflict with speech cues"),
            ):
                self.inspect()
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

    def test_captionless_path_requires_accepted_consistent_hash_bound_edit(self):
        for change in ("rejected", "wrong-duration", "changed-edit", "missing-transcript", "wrong-source"):
            self.captionless_export()
            if change == "rejected":
                self.captionless_export(verdict="reject")
            elif change == "wrong-duration":
                self.captionless_export(keep=[{"start": 0, "end": 4}])
            elif change == "changed-edit":
                (self.export / "edit.json").write_text("{}")
            elif change == "missing-transcript":
                (self.export / "transcript.json").unlink()
            else:
                write_json(self.export / "transcript.json", {"words": [], "source_sha256": "a" * 64})
                manifest = read_json(self.export / "manifest.json")
                manifest["transcript_sha256"] = sha256(self.export / "transcript.json")
                manifest["artifact_sha256"]["transcript.json"] = manifest["transcript_sha256"]
                write_json(self.export / "manifest.json", manifest)
            with self.subTest(change=change), self.assertRaises(RelayError):
                self.inspect()
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

    def test_nonempty_but_malformed_captions_do_not_use_technical_fallback(self):
        self.caption_text = "!!!"
        self.write_export()
        with self.assertRaisesRegex(RelayError, "no bounded recognizable word sequence"):
            self.inspect()
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

    def test_captionless_technical_defects_still_fail_without_fabricated_agreement(self):
        for changes in (
            {"full_decode": "failed"},
            {"warnings": ["Audio defect"]},
            {"audio_silent": True},
            {"loudness": {"input_i": "-16", "input_tp": "0"}},
            {"expected_duration": 4},
        ):
            old = copy.deepcopy(self.qa)
            self.qa.update(changes)
            self.write_export()
            self.captionless_export()
            with self.subTest(changes=changes):
                path, asr = self.inspect()
                asr.assert_not_called()
                packet = read_verified(path, self.candidate["output_hash"], self.root)
                self.assertFalse(packet["passed"])
                self.assertFalse(all(packet["checks"].values()))
                self.assertTrue(packet["assessment"]["audio_issues"])
                self.assertEqual(packet["assessment"]["confidence"], 0)
                self.assertIsNone(packet["metrics"]["caption_word_coverage"])
            self.qa = old

    def test_technical_receipt_cannot_claim_speech_or_hide_failed_checks(self):
        self.captionless_export()
        path, _ = self.inspect()
        original = read_json(path)
        for change in ("confidence", "coverage", "check", "incomplete", "speech-assessed", "wrong-type"):
            packet = copy.deepcopy(original)
            if change == "confidence":
                packet["assessment"]["confidence"] = 1
            elif change == "coverage":
                packet["metrics"]["caption_word_coverage"] = 1
            elif change == "check":
                packet["checks"]["full_decode"] = False
            elif change == "incomplete":
                packet["complete"] = False
            elif change == "speech-assessed":
                packet["speech_assessed"] = True
            else:
                packet["check_type"] = "rendered-speech"
            write_json(path, packet)
            with self.subTest(change=change), self.assertRaisesRegex(RelayError, "Technical audio receipt"):
                read_verified(path, self.candidate["output_hash"], self.root)

    def test_mismatched_or_missing_caption_artifact_fails_before_asr(self):
        (self.export / "captions.json").write_text('{"cues":[]}')
        with (
            patch("reelrelay.audio_review.BuiltinStudio.transcribe_audio") as asr,
            self.assertRaises(RelayError),
        ):
            inspect_rendered_speech(self.cfg, self.candidate)
        asr.assert_not_called()
        (self.export / "captions.json").unlink()
        with self.assertRaises(RelayError):
            inspect_rendered_speech(self.cfg, self.candidate)
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

    def test_export_changed_before_or_during_check_never_yields_report(self):
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(RelayError, "changed before"):
            self.inspect()
        self.source.write_bytes(b"independent rendered audio fixture")

        def changed(*args):
            result = self.transcribe(*args)
            self.source.write_bytes(b"changed while transcribing")
            return result

        with self.assertRaisesRegex(RelayError, "changed during"):
            self.inspect(transcribe=changed)
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

    def test_asr_failure_leaves_failure_receipt_without_report(self):
        def failed(*args):
            raise RelayError("ASR unavailable")

        with self.assertRaisesRegex(RelayError, "ASR unavailable"):
            self.inspect(transcribe=failed)
        self.assertTrue(list((self.root / "perception").glob("*/failure.json")))
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

    def test_decode_silence_peak_and_qa_warnings_fail_despite_word_agreement(self):
        for changes in (
            {"full_decode": "failed"},
            {"warnings": ["Audio defect"]},
            {"audio_silent": True},
            {"loudness": {"input_i": "-16", "input_tp": "0"}},
            {"loudness": {"input_i": "-inf", "input_tp": "-inf"}},
        ):
            old = copy.deepcopy(self.qa)
            self.qa.update(changes)
            self.write_export()
            with self.subTest(changes=changes):
                path, _ = self.inspect()
                packet = read_json(path)
                self.assertEqual(packet["assessment"]["confidence"], 1)
                self.assertFalse(packet["passed"])
                self.assertTrue(packet["assessment"]["audio_issues"])
            self.qa = old

    def test_public_asr_helper_retains_raw_runner_and_model_provenance(self):
        engine = BuiltinStudio(self.cfg)
        engine.ensure()
        engine.python.parent.mkdir(parents=True)
        engine.python.write_text("test interpreter")
        engine.model_path().mkdir(parents=True)
        (engine.model_path() / "model.bin").write_bytes(b"test weights")
        wav = self.root / "canonical.wav"
        wav.write_bytes(b"fixture")
        folder = self.root / "perception/helper"

        def runner(args, root, **kwargs):
            write_json(args[args.index("--output") + 1], {"words": [{"start": 0, "end": 1, "text": "heard"}]})

        with patch("reelrelay.engine.backend.media.run", side_effect=runner):
            transcript, provenance = engine.transcribe_audio(wav, folder, 5, self.candidate["output_hash"])
        self.assertEqual(transcript["source_sha256"], self.candidate["output_hash"])
        self.assertTrue(provenance["local_files_only"])
        self.assertEqual(provenance["raw_sha256"], sha256(provenance["raw_path"]))
        self.assertEqual(
            provenance["model_file_sha256"]["model.bin"], sha256(engine.model_path() / "model.bin")
        )
        self.assertEqual(provenance["runner_sha256"], sha256(provenance["runner_path"]))


if __name__ == "__main__":
    unittest.main()
