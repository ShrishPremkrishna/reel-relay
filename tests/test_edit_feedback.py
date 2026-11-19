import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from reelrelay.config import DEFAULTS
from reelrelay.server import create_app
from reelrelay.store import LeaseLost, Store
from reelrelay.util import RelayError, read_json, sha256, write_json
from reelrelay.worker import _run_stage, request_changes


class EditFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.store = Store(self.cfg)
        self.export = self.root / "original-export.mp4"
        self.export.write_bytes(b"unchanged-export-for-lifecycle-test")
        evidence = self.root / "evidence.json"
        write_json(evidence, {"images": [], "duration": 20, "assets": [], "stage": "select"})
        self.cid = self.store.add("example", self.export, sha256(self.export))
        self.store.update(
            self.cid,
            status="approved",
            output_path=str(self.export),
            output_hash=sha256(self.export),
            evidence_path=str(evidence),
            review={"approved": True},
            metadata={"revisions": 2},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_request_removes_publish_approval_and_preserves_export(self):
        before = self.store.get(self.cid)
        changed = request_changes(self.cfg, self.cid, "Hold the reaction longer.", before["updated_at"])
        self.assertEqual(changed["status"], "preview_changes")
        self.assertFalse(changed["review"]["approved"])
        self.assertEqual(changed["metadata"]["revisions"], 0)
        self.assertEqual(changed["metadata"]["edit_feedback"][0]["notes"], "Hold the reaction longer.")
        self.assertEqual(changed["output_hash"], before["output_hash"])
        self.assertEqual(sha256(self.export), before["output_hash"])

    def test_active_publication_stale_version_and_blank_feedback_are_rejected(self):
        before = self.store.get(self.cid)
        self.store.update(self.cid, caption="New caption, same status")
        with self.assertRaises(LeaseLost):
            request_changes(self.cfg, self.cid, "Remove the boom", before["updated_at"])
        with self.assertRaises(LeaseLost):
            self.store.update(
                self.cid, expected="approved", expected_version=before["updated_at"], status="rejected"
            )
        for status in ("publishing", "published", "publish_unknown", "rendering"):
            self.store.update(self.cid, status=status)
            with self.assertRaises(RelayError):
                request_changes(self.cfg, self.cid, "Remove the boom", self.store.get(self.cid)["updated_at"])
        with self.assertRaises(RelayError):
            request_changes(self.cfg, self.cid, "   ", self.store.get(self.cid)["updated_at"])

    def test_browser_feedback_requires_csrf_and_exposes_availability(self):
        with TestClient(create_app(self.cfg), base_url="http://127.0.0.1:8765") as client:
            state = client.get("/api/state").json()
            self.assertTrue(state["candidates"][0]["can_request_changes"])
            body = {
                "id": self.cid,
                "notes": "Use fewer effects",
                "version": self.store.get(self.cid)["updated_at"],
            }
            self.assertEqual(client.post("/api/request-changes", json=body).status_code, 403)
            headers = {"Origin": "http://127.0.0.1:8765", "X-Relay-Token": state["settings"]["csrf"]}
            self.assertEqual(client.post("/api/request-changes", json=body, headers=headers).status_code, 200)
            self.assertEqual(self.store.get(self.cid)["status"], "preview_changes")

    def test_operator_notes_reach_the_editor_on_the_next_revision(self):
        request_changes(
            self.cfg, self.cid, "Keep the pause before the payoff", self.store.get(self.cid)["updated_at"]
        )
        candidate = self.store.claim("preview_changes", "revising", cid=self.cid)

        def judge(_cfg, packet_path):
            packet = read_json(packet_path)
            self.assertEqual(packet["operator_feedback"][-1]["notes"], "Keep the pause before the payoff")
            self.assertIn("Keep the pause", packet["preview_feedback"]["notes"])
            return {
                "verdict": "reject",
                "score": 3,
                "confidence": 0.9,
                "reason": "No usable payoff",
                "hook": "",
                "caption": "",
                "flags": [],
                "layout": "contain",
                "facecam": None,
                "keep": [],
                "sfx": [],
                "overlays": [],
            }

        with patch("reelrelay.worker.judge", side_effect=judge):
            _run_stage(self.cfg, self.store, None, candidate)
        self.assertEqual(self.store.get(self.cid)["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
