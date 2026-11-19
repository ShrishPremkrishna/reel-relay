import copy
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from reelrelay.config import DEFAULTS
from reelrelay.server import create_app
from reelrelay.store import LeaseLost, Store
from reelrelay.util import RelayError, sha256, write_json
from reelrelay.worker import record_operator_review


class OperatorReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.cfg["publish"]["auto_approve"] = True
        self.store = Store(self.cfg)
        self.export = self.root / "export.mp4"
        self.export.write_bytes(b"finished-export-for-lifecycle-test")
        self.cid = self.store.add("example", self.export, sha256(self.export))
        self.store.update(
            self.cid,
            status="needs_human_review",
            output_path=str(self.export),
            output_hash=sha256(self.export),
            review={"approved": False, "issues": ["Listen to the ending"]},
        )
        write_json(self.root / "qa.json", {"full_decode": "passed", "warnings": []})

    def tearDown(self):
        self.temp.cleanup()

    def review(self):
        return record_operator_review(
            self.cfg,
            self.cid,
            "Checked the ending and the effect level",
            self.store.get(self.cid)["updated_at"],
        )

    def test_operator_check_keeps_publication_as_a_separate_step(self):
        item = self.review()
        self.assertEqual(item["status"], "ready")
        self.assertEqual(item["review"]["audio_review"], "operator_confirmed")
        self.assertTrue(item["review"]["approved"])
        self.assertEqual(
            item["metadata"]["operator_reviews"][-1]["previous_review"]["issues"], ["Listen to the ending"]
        )
        self.assertIsNone(self.store.claim("approved", "uploading", cid=self.cid))

    def test_technical_failure_missing_qa_and_changed_export_cannot_be_overridden(self):
        for qa in (
            {"full_decode": "failed", "warnings": []},
            {"full_decode": "passed", "warnings": ["clipping"]},
            {},
        ):
            write_json(self.root / "qa.json", qa)
            with self.assertRaises(RelayError):
                self.review()
        (self.root / "qa.json").unlink()
        with self.assertRaises(RelayError):
            self.review()
        write_json(self.root / "qa.json", {"full_decode": "passed", "warnings": []})
        self.export.write_bytes(b"changed-file")
        with self.assertRaises(RelayError):
            self.review()

    def test_stale_or_active_job_cannot_be_overridden(self):
        old = self.store.get(self.cid)["updated_at"]
        self.store.update(self.cid, caption="New caption")
        with self.assertRaises(LeaseLost):
            record_operator_review(self.cfg, self.cid, "Checked the output", old)
        for status in ("reviewing", "revising", "rendering", "publishing", "publish_unknown", "published"):
            self.store.update(self.cid, status=status)
            with self.assertRaises(RelayError):
                self.review()

    def test_browser_requires_both_viewing_and_listening_confirmation(self):
        with TestClient(create_app(self.cfg), base_url="http://127.0.0.1:8765") as client:
            state = client.get("/api/state").json()
            headers = {"Origin": "http://127.0.0.1:8765", "X-Relay-Token": state["settings"]["csrf"]}
            body = {
                "id": self.cid,
                "version": self.store.get(self.cid)["updated_at"],
                "notes": "Checked the output",
                "watched": True,
                "listened": False,
            }
            self.assertEqual(client.post("/api/operator-review", json=body).status_code, 403)
            self.assertEqual(client.post("/api/operator-review", json=body, headers=headers).status_code, 422)
            body["watched"], body["listened"] = False, True
            self.assertEqual(client.post("/api/operator-review", json=body, headers=headers).status_code, 422)
            body["watched"] = True
            self.assertEqual(client.post("/api/operator-review", json=body, headers=headers).status_code, 200)
            self.assertEqual(self.store.get(self.cid)["status"], "ready")
