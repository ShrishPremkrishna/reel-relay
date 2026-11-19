import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_pipeline import decision

from reelrelay import media
from reelrelay.agent import validate_decision
from reelrelay.config import DEFAULTS, Web
from reelrelay.server import create_app
from reelrelay.store import LeaseLost, Store
from reelrelay.util import RelayError, TransientError, sha256, write_json
from reelrelay.worker import apply_decision, apply_review, reassess, step


class ProductWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.store = Store(self.cfg)
        self.source = self.root / "sources/clip.mp4"
        self.source.parent.mkdir()
        self.source.write_bytes(b"unit fixture, not real footage")
        self.cid = self.store.add("fixture", self.source, sha256(self.source), end=30)
        self.client = TestClient(create_app(self.cfg), base_url="http://127.0.0.1:8765")
        csrf = self.client.get("/api/state").json()["settings"]["csrf"]
        self.headers = {"Origin": "http://127.0.0.1:8765", "X-Relay-Token": csrf}

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def test_stale_approval_and_rejection_cannot_target_new_export(self):
        self.store.update(
            self.cid,
            status="ready",
            output_path=str(self.source),
            output_hash=sha256(self.source),
            review={"approved": True},
        )
        version = self.store.get(self.cid)["updated_at"]
        self.store.update(self.cid, caption="A different edit arrived")
        for route in ("approve", "reject"):
            response = self.client.post(
                "/api/" + route, json={"id": self.cid, "version": version}, headers=self.headers
            )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.store.get(self.cid)["status"], "ready")

    def test_reassessment_preserves_decision_and_does_not_accept_source(self):
        evidence = self.root / "evidence.json"
        write_json(evidence, {"duration": 30, "assets": []})
        self.store.update(
            self.cid, status="editorial_review", evidence_path=str(evidence), decision=decision()
        )
        version = self.store.get(self.cid)["updated_at"]
        reassess(self.cfg, self.cid, version, "Listen to the final reply")
        item = self.store.get(self.cid)
        self.assertEqual(item["status"], "needs_agent")
        self.assertEqual(item["metadata"]["selection_history"][0]["decision"], decision())
        self.assertEqual(item["metadata"]["reassessment_notes"], "Listen to the final reply")

    def test_reassessment_rejects_stale_context(self):
        self.store.update(self.cid, status="editorial_review", evidence_path=str(self.root / "evidence.json"))
        version = self.store.get(self.cid)["updated_at"]
        self.store.update(self.cid, caption="Changed")
        with self.assertRaises(LeaseLost):
            reassess(self.cfg, self.cid, version)

    def test_image_only_preview_never_enters_automatic_publish_queue(self):
        self.cfg["publish"]["auto_approve"] = True
        self.store.update(
            self.cid,
            status="needs_preview_review",
            output_path=str(self.source),
            output_hash=sha256(self.source),
        )
        apply_review(
            self.cfg,
            self.cid,
            {
                "approved": True,
                "confidence": 0.99,
                "issues": [],
                "notes": "Images look good",
                "audio_review": "not_listened",
            },
        )
        self.assertEqual(self.store.get(self.cid)["status"], "needs_human_review")

    def test_audio_receipt_must_match_exact_export(self):
        receipt = self.root / "perception/report.json"
        write_json(receipt, {"schema_version": 1, "source_sha256": "wrong-export", "modalities": ["audio"]})
        self.store.update(
            self.cid,
            status="needs_preview_review",
            output_path=str(self.source),
            output_hash=sha256(self.source),
            metadata={
                "preview_perception": "perception/report.json",
                "preview_perception_hash": sha256(receipt),
            },
        )
        with self.assertRaises(RelayError):
            apply_review(
                self.cfg,
                self.cid,
                {
                    "approved": True,
                    "confidence": 0.99,
                    "issues": [],
                    "notes": "Good",
                    "audio_review": "not_listened",
                },
            )
        self.assertEqual(self.store.get(self.cid)["status"], "needs_preview_review")

    def test_verified_nonspeech_audio_needs_a_visual_pass_and_explicit_volume_mode(self):
        self.cfg["publish"]["auto_approve"] = True
        receipt = self.root / "perception/report.json"
        write_json(receipt, {"fixture": "validation tested by audio-review suite"})
        technical = {
            "provider": "local-technical",
            "model": "ffmpeg",
            "check_type": "technical-audio",
            "passed": True,
            "speech_assessed": False,
            "assessment": {"confidence": 0, "audio_issues": [], "visual_issues": []},
        }
        for mode, passed, visual, expected in (
            ("throughput", True, True, "approved"),
            ("balanced", True, True, "needs_human_review"),
            ("throughput", False, True, "needs_human_review"),
            ("throughput", True, False, "preview_changes"),
        ):
            with self.subTest(mode=mode, technical_pass=passed, visual_pass=visual):
                self.cfg["agent"]["selection_mode"] = mode
                technical["passed"] = passed
                self.store.update(
                    self.cid,
                    status="needs_preview_review",
                    output_path=str(self.source),
                    output_hash=sha256(self.source),
                    metadata={
                        "preview_perception": "perception/report.json",
                        "preview_perception_hash": sha256(receipt),
                    },
                )
                with patch("reelrelay.worker.read_verified", return_value=technical):
                    apply_review(
                        self.cfg,
                        self.cid,
                        {
                            "approved": visual,
                            "confidence": 0.9,
                            "issues": [] if visual else ["The reaction is cropped out"],
                            "notes": "Visual assessment only",
                            "audio_review": "not_listened",
                        },
                    )
                item = self.store.get(self.cid)
                self.assertEqual(item["status"], expected)
                self.assertEqual(item["review"]["audio_review"], "technical_assessed")

    def test_activity_does_not_return_raw_upstream_error(self):
        self.store.event("job_failed", {"reason": "https://example.invalid/?token=private-token"}, self.cid)
        for route in ("/api/activity", "/api/state"):
            response = self.client.get(route)
            self.assertNotIn("private-token", response.text)
        self.assertEqual(self.client.get("/api/activity?limit=101").status_code, 422)

    def test_editor_cannot_drop_essential_payoff_or_reorder_dialogue(self):
        value = decision()
        value["story"] = {
            "setup": "question",
            "payoff": "reply",
            "why_funny": "contradiction",
            "share_reason": "friend",
            "essential_beats": [{"start": 14, "end": 16, "description": "payoff"}],
        }
        with self.assertRaises(RelayError):
            validate_decision(value, {"duration": 30, "assets": []})
        value.pop("story")
        value["keep"] = [value["keep"][0], {**value["keep"][0], "start": 1, "end": 3}]
        with self.assertRaises(RelayError):
            validate_decision(value, {"duration": 30, "assets": []})

    def test_long_required_credits_hold_before_rendering(self):
        evidence = self.root / "evidence.json"
        write_json(evidence, {"duration": 30, "assets": []})
        credit_source = self.root / "sources/credit-fixture.mp4"
        credit_source.write_bytes(b"distinct credit-length fixture")
        cid = self.store.add(
            "credit-fixture", credit_source, sha256(credit_source), end=30, attribution="C" * 500
        )
        self.store.update(cid, status="needs_agent", evidence_path=str(evidence))
        value = decision()
        value["caption"] = "A" * 1800
        item = apply_decision(self.cfg, cid, value)
        self.assertEqual(item["status"], "editorial_review")
        self.assertIn("credits", item["error"])
        self.assertIsNone(item["output_path"])

    def test_volume_mode_accepts_modest_score_but_preserves_concrete_holds(self):
        evidence = self.root / "evidence.json"
        write_json(evidence, {"duration": 30, "assets": []})
        self.cfg["agent"].update(selection_mode="throughput", min_confidence=0.6)
        value = decision()
        value.update(score=5, confidence=0.7)
        for flags, verdict, expected in (
            ([], "accept", "selected"),
            (["incomprehensible_speech"], "accept", "editorial_review"),
            ([], "needs_review", "editorial_review"),
        ):
            with self.subTest(flags=flags, verdict=verdict):
                self.store.update(self.cid, status="needs_agent", evidence_path=str(evidence))
                value.update(flags=flags, verdict=verdict)
                self.assertEqual(apply_decision(self.cfg, self.cid, value)["status"], expected)

    def test_transient_editor_failure_does_not_consume_revision(self):
        evidence = self.root / "evidence.json"
        write_json(evidence, {"duration": 30, "assets": [], "images": []})
        self.cfg["agent"]["provider"] = "command"
        self.store.update(
            self.cid,
            status="preview_changes",
            evidence_path=str(evidence),
            output_path=str(self.source),
            decision=decision(),
            review={"issues": ["Shorten the caption"]},
            metadata={"revisions": 0},
        )
        with patch("reelrelay.worker.judge", side_effect=TransientError("temporary outage")):
            step(self.cfg, cid=self.cid, stage="revising")
        item = self.store.get(self.cid)
        self.assertEqual(item["status"], "preview_changes")
        self.assertEqual(item["metadata"]["revisions"], 0)

    def test_editor_can_request_one_detail_pass_without_granting_acceptance(self):
        evidence = self.root / "evidence/source.json"
        write_json(evidence, {"stage": "select", "duration": 30, "assets": [], "images": []})
        self.cfg["agent"]["provider"] = "command"
        self.store.update(self.cid, status="needs_agent", evidence_path=str(evidence))
        value = decision()
        value.update(
            verdict="needs_review",
            keep=[],
            inspection_windows=[{"start": 5, "end": 8, "reason": "Inspect the fall between frames"}],
        )
        with (
            patch("reelrelay.worker.judge", return_value=value),
            patch("reelrelay.worker.media.detail_contact_sheets", return_value=[]) as details,
        ):
            step(self.cfg, cid=self.cid, stage="judging")
            first = self.store.get(self.cid)
            self.assertEqual(first["status"], "needs_agent")
            self.assertEqual(first["metadata"]["detail_passes"], 1)
            self.assertIsNone(first["output_path"])
            step(self.cfg, cid=self.cid, stage="judging")
        self.assertEqual(details.call_count, 1)
        self.assertEqual(self.store.get(self.cid)["status"], "editorial_review")

    def test_detail_windows_cannot_request_unbounded_decode_evidence(self):
        value = decision()
        for window in (
            {"start": 0, "end": 20, "reason": "too long"},
            {"start": 20, "end": 31, "reason": "outside source"},
        ):
            value["inspection_windows"] = [window]
            with self.subTest(window=window), self.assertRaises(RelayError):
                validate_decision(value, {"duration": 30, "assets": []})

    def test_non_ascii_login_fails_without_server_error(self):
        cfg = copy.deepcopy(self.cfg)
        cfg["web"]["public_base_url"] = "https://relay.example.com"
        with (
            patch.dict(os.environ, {"REEL_RELAY_WEB_TOKEN": "x" * 32}),
            TestClient(create_app(cfg), base_url="https://relay.example.com") as client,
        ):
            result = client.post(
                "/api/login",
                json={"token": "incorrect 🔑"},
                headers={"Origin": "https://relay.example.com"},
            )
            self.assertEqual(result.status_code, 401)

    def test_public_url_must_be_an_origin(self):
        for value in (
            "https://relay.example.com/prefix",
            "https://relay.example.com/?secret=x",
            "https://name:pass@relay.example.com",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Web(public_base_url=value)
        self.assertEqual(
            Web(public_base_url="https://relay.example.com/").public_base_url, "https://relay.example.com"
        )

    def test_linux_data_root_and_future_database_sidecars_are_read_only(self):
        (self.root / "engine").mkdir()
        with (
            patch.object(media.sys, "platform", "linux"),
            patch.object(media.shutil, "which", return_value="/usr/bin/bwrap"),
        ):
            args = media.command(["ffprobe", str(self.source)], self.root)
        root = str(self.root.resolve())
        self.assertIn(["--tmpfs", root], [args[i : i + 2] for i in range(len(args))])
        self.assertIn(["--remount-ro", root], [args[i : i + 2] for i in range(len(args))])
        self.assertNotIn(["--ro-bind", root, root], [args[i : i + 3] for i in range(len(args))])
        self.assertNotIn(["--bind", root, root], [args[i : i + 3] for i in range(len(args))])
        self.assertIn(
            ["--bind", root + "/engine", root + "/engine"], [args[i : i + 3] for i in range(len(args))]
        )


@unittest.skipUnless(os.environ.get("REEL_RELAY_MEDIA_TESTS") == "1", "Opt in to real sandbox checks")
class MediaIntegrityBoundaryTests(unittest.TestCase):
    def test_decoder_cannot_modify_control_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "artifacts").mkdir()
            for name in (".private/secret", "queue.sqlite3", "queue.sqlite3-wal", "config.toml"):
                with self.subTest(name=name), self.assertRaises(RelayError):
                    media.run(["/usr/bin/touch", str(root / name)], root)
            media.run(["/usr/bin/touch", str(root / "artifacts/allowed")], root)
            self.assertTrue((root / "artifacts/allowed").exists())
