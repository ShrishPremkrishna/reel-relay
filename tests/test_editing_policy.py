"""Operator editing choices are validated, applied and frozen per selected job."""

import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from reelrelay.agent import validate_decision
from reelrelay.config import DEFAULTS, Editing, Settings
from reelrelay.content_flags import CONTENT_FLAGS, KNOWN_FLAGS, held_flags
from reelrelay.engine.captions import caption_cues, write_subtitles
from reelrelay.store import Store
from reelrelay.util import RelayError, sha256, write_json
from reelrelay.worker import apply_decision, work
from tests.test_pipeline import decision


class EditingContracts(unittest.TestCase):
    def setUp(self):
        self.evidence = {
            "duration": 30,
            "assets": [
                {"path": "tone.wav", "kind": "sfx", "duration": 2},
                {"path": "reaction.mp4", "kind": "broll", "duration": 2},
            ],
        }

    def test_legacy_configs_receive_explicit_safe_defaults(self):
        settings = Settings.model_validate({})
        self.assertEqual((settings.editing.min_seconds, settings.editing.max_seconds), (5, 60))
        self.assertEqual(settings.editing.caption_words, 3)
        self.assertTrue(settings.editing.captions_enabled)
        self.assertEqual(settings.editing.layout, "auto")
        self.assertFalse(settings.agent.allow_cloud_upload)
        self.assertEqual(Settings(agent={"provider": "gemini"}).agent.model, "")

    def test_invalid_bounds_types_and_conflicting_duration_are_rejected(self):
        for values in (
            {"min_seconds": 4},
            {"max_seconds": 91},
            {"min_seconds": 61},
            {"min_seconds": True},
            {"caption_words": 0},
            {"caption_words": 7},
            {"max_sfx": 13},
            {"max_overlays": 9},
            {"sfx_gain_db": -5},
            {"sfx_gain_db": -37},
            {"layout": "unknown"},
            {"style_notes": "x" * 2001},
        ):
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Editing.model_validate(values)

    def test_finished_length_uses_configured_range(self):
        self.assertEqual(
            validate_decision(decision(), self.evidence, editing={"min_seconds": 8, "max_seconds": 12})[
                "verdict"
            ],
            "accept",
        )
        for values in ({"min_seconds": 11, "max_seconds": 12}, {"max_seconds": 9}):
            with self.subTest(values=values), self.assertRaisesRegex(RelayError, "Accepted edits"):
                validate_decision(decision(), self.evidence, editing=values)
        rejected = decision()
        rejected.update(verdict="reject", keep=[])
        self.assertEqual(validate_decision(rejected, self.evidence, editing={"min_seconds": 50})["keep"], [])

    def test_disabled_effects_fixed_gain_and_limits_are_enforced_without_mutating_input(self):
        value = decision()
        value["sfx"] = [
            {"asset_path": "tone.wav", "at": 1, "duration": 1, "gain_db": -12, "reason": "reaction"}
        ]
        value["overlays"] = [
            {
                "asset_path": "reaction.mp4",
                "at": 2,
                "duration": 1,
                "source_start": 0,
                "width_fraction": 0.3,
                "height_fraction": 0.3,
                "x": 0.5,
                "y": 0.5,
                "reason": "reaction",
            }
        ]
        frozen = copy.deepcopy(value)
        effective = validate_decision(
            value, self.evidence, editing={"sfx_gain_db": -26, "overlays_enabled": False}
        )
        self.assertEqual(effective["sfx"][0]["gain_db"], -26)
        self.assertEqual(effective["overlays"], [])
        self.assertEqual(value, frozen)
        disabled = validate_decision(value, self.evidence, editing={"sfx_enabled": False, "max_overlays": 0})
        self.assertEqual((disabled["sfx"], disabled["overlays"]), ([], []))
        value["sfx"] *= 2
        with self.assertRaisesRegex(RelayError, "maximum sfx"):
            validate_decision(value, self.evidence, editing={"max_sfx": 1})

    def test_forced_layout_preserves_valid_framing_and_never_invents_facecam(self):
        value = decision()
        forced = validate_decision(value, self.evidence, editing={"layout": "focus"})
        self.assertEqual(forced["layout"], "focus")
        with self.assertRaisesRegex(RelayError, "real facecam"):
            validate_decision(value, self.evidence, editing={"layout": "split"})
        value["facecam"] = {"x": 0, "y": 0, "width": 0.3, "height": 0.3}
        self.assertEqual(
            validate_decision(value, self.evidence, editing={"layout": "split"})["layout"], "split"
        )

    def test_caption_toggle_keeps_metadata_and_sidecar_but_removes_burned_speech(self):
        words = [
            {"start": index * 0.2, "end": (index + 1) * 0.2, "text": text}
            for index, text in enumerate(["A", "compact", "caption", "group"])
        ]
        self.assertTrue(all(len(row["text"].split()) <= 2 for row in caption_cues(words, maximum_words=2)))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            cues = caption_cues(words)
            write_subtitles(folder, cues, "A hook", 5, render_captions=False)
            self.assertNotIn("Dialogue: 0", (folder / "captions.ass").read_text())
            self.assertIn("Dialogue: 1", (folder / "captions.ass").read_text())
            self.assertIn("A compact caption", (folder / "captions.srt").read_text())

    def test_unknown_flags_are_held_even_when_allow_list_contains_them(self):
        self.assertEqual(len(CONTENT_FLAGS), len(KNOWN_FLAGS))
        self.assertEqual(held_flags(["strong_language"], ["strong_language"]), set())
        self.assertEqual(held_flags(["new_unknown_flag"], ["new_unknown_flag"]), {"new_unknown_flag"})
        self.assertEqual(held_flags(["slurs"], []), {"slurs"})
        self.assertEqual(held_flags(["slurs"], ["slurs"]), set())


class EditingWorkerContracts(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.store = Store(self.cfg)
        source = self.root / "source.mp4"
        source.write_bytes(b"fixture only")
        self.cid = self.store.add("fixture", source, sha256(source), end=30)
        evidence = self.root / "evidence.json"
        write_json(evidence, {"duration": 30, "assets": []})
        self.store.update(self.cid, status="needs_agent", evidence_path=str(evidence))

    def tearDown(self):
        self.temporary.cleanup()

    def test_normal_worker_freezes_preferences_and_holds_unknown_flags(self):
        self.cfg["editing"].update(layout="focus", captions_enabled=False)
        apply_decision(self.cfg, self.cid, decision())
        item = self.store.get(self.cid)
        self.assertEqual(item["status"], "selected")
        self.assertEqual(item["decision"]["layout"], "focus")
        self.assertFalse(item["metadata"]["editing_settings"]["captions_enabled"])
        self.cfg["editing"]["captions_enabled"] = True
        self.assertFalse(self.store.get(self.cid)["metadata"]["editing_settings"]["captions_enabled"])
        self.store.update(self.cid, status="needs_agent")
        value = decision()
        value["flags"] = ["unknown_future_flag"]
        self.cfg["agent"]["allowed_flags"] = value["flags"]
        apply_decision(self.cfg, self.cid, value)
        self.assertEqual(self.store.get(self.cid)["status"], "editorial_review")

    def test_watch_pause_stops_before_claiming_but_manual_work_stays_available(self):
        stop = threading.Event()
        with (
            patch("reelrelay.worker.service_enabled", return_value=False),
            patch("reelrelay.worker.step") as step,
            patch.object(stop, "wait", side_effect=lambda _: stop.set()) as wait,
        ):
            work(self.cfg, stop, watch=True)
        step.assert_not_called()
        wait.assert_called_once_with(2)
        with (
            patch("reelrelay.worker.service_enabled") as enabled,
            patch("reelrelay.worker.step", return_value=None) as step,
        ):
            work(self.cfg, threading.Event(), watch=False)
        enabled.assert_not_called()
        step.assert_called_once()


if __name__ == "__main__":
    unittest.main()
