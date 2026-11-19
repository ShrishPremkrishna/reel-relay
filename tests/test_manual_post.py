import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from reelrelay.config import DEFAULTS
from reelrelay.server import create_app
from reelrelay.store import LeaseLost, Store
from reelrelay.util import RelayError, sha256
from reelrelay.worker import record_manual_post


class ManualPostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.store = Store(self.cfg)
        self.export = self.root / "export.mp4"
        self.export.write_bytes(b"reviewed-export-for-lifecycle-test")
        self.cid = self.store.add("example", self.export, sha256(self.export))
        self.store.update(
            self.cid,
            status="approved",
            output_path=str(self.export),
            output_hash=sha256(self.export),
            review={"approved": True},
        )

    def tearDown(self):
        self.temp.cleanup()

    def record(self, link="https://www.instagram.com/reel/Example123/?utm_source=test"):
        return record_manual_post(self.cfg, self.cid, link, self.store.get(self.cid)["updated_at"])

    def test_record_prevents_later_publication_and_labels_unverified_link(self):
        item = self.record()
        self.assertEqual(item["status"], "published")
        self.assertEqual(item["permalink"], "https://www.instagram.com/reel/Example123/")
        self.assertFalse(item["metadata"]["publication_verified_by_api"])
        self.assertEqual(item["metadata"]["publication_method"], "manual")
        self.assertIsNone(self.store.claim("approved", "uploading", cid=self.cid))
        with self.assertRaises(RelayError):
            self.record()

    def test_invalid_links_unreviewed_and_changed_exports_are_blocked(self):
        for link in (
            "http://instagram.com/reel/Example123/",
            "https://instagram.com.attacker.invalid/reel/Example123/",
            "https://attacker@instagram.com/reel/Example123/",
            "https://instagram.com/example/",
            "https://instagram.com/reel/../",
        ):
            with self.assertRaises(RelayError):
                self.record(link)
        fixture = self.store.add("example", self.export, "fixture-hash", is_test=True)
        self.store.update(fixture, status="ready", review={"approved": True})
        with self.assertRaises(RelayError):
            record_manual_post(
                self.cfg,
                fixture,
                "https://instagram.com/reel/Example123/",
                self.store.get(fixture)["updated_at"],
            )
        for changes in (
            {"review": {"approved": False}},
            {"status": "publish_unknown"},
            {"status": "uploading"},
            {"status": "needs_human_review"},
        ):
            self.store.update(self.cid, status="ready", review={"approved": True})
            self.store.update(self.cid, **changes)
            with self.assertRaises(RelayError):
                self.record()
        self.store.update(self.cid, status="ready")
        self.export.write_bytes(b"different-file")
        with self.assertRaises(RelayError):
            self.record()

    def test_stale_version_and_publisher_claim_win_over_manual_record(self):
        old = self.store.get(self.cid)["updated_at"]
        self.store.update(self.cid, caption="Changed")
        with self.assertRaises(LeaseLost):
            record_manual_post(self.cfg, self.cid, "https://instagram.com/reel/Example123/", old)
        original = Store.update

        def raced_update(store, cid, **kwargs):
            if kwargs.get("status") == "published":
                self.store.claim("approved", "uploading", cid=cid)
            return original(store, cid, **kwargs)

        with patch.object(Store, "update", raced_update), self.assertRaises(LeaseLost):
            self.record()
        self.assertEqual(self.store.get(self.cid)["status"], "uploading")

    def test_browser_requires_explicit_confirmation_and_csrf(self):
        with TestClient(create_app(self.cfg), base_url="http://127.0.0.1:8765") as client:
            state = client.get("/api/state").json()
            body = {
                "id": self.cid,
                "permalink": "https://instagram.com/reel/Example123/",
                "version": self.store.get(self.cid)["updated_at"],
                "confirmed": True,
            }
            self.assertEqual(client.post("/api/record-manual-post", json=body).status_code, 403)
            headers = {"Origin": "http://127.0.0.1:8765", "X-Relay-Token": state["settings"]["csrf"]}
            body["confirmed"] = False
            self.assertEqual(
                client.post("/api/record-manual-post", json=body, headers=headers).status_code, 422
            )
            body["confirmed"] = True
            result = client.post("/api/record-manual-post", json=body, headers=headers)
            self.assertEqual(result.status_code, 200)
            self.assertFalse(result.json()["api_verified"])
            self.assertTrue(client.get("/api/state").json()["candidates"][0]["posted_manually"])
