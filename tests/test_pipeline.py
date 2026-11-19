import concurrent.futures
import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from reelrelay import db as schema
from reelrelay.agent import validate_decision, validate_review
from reelrelay.config import DEFAULTS
from reelrelay.detector import ChatMessage, SpikeDetector
from reelrelay.publish import publish_one, reconcile
from reelrelay.server import create_app
from reelrelay.store import LeaseLost, Store
from reelrelay.twitch import parse_irc
from reelrelay.util import RelayError, sha256


def decision():
    return {
        "verdict": "accept",
        "score": 8,
        "confidence": 0.9,
        "reason": "Complete story",
        "hook": "A shortcut",
        "caption": "The wrong turn",
        "flags": [],
        "layout": "contain",
        "facecam": None,
        "keep": [
            {"start": 2, "end": 12, "reason": "setup and payoff", "focus_x": 0.5, "focus_y": 0.5, "zoom": 1}
        ],
        "sfx": [],
        "overlays": [],
    }


class DetectorTests(unittest.TestCase):
    def setUp(self):
        cfg = copy.deepcopy(DEFAULTS["detector"])
        cfg.update(window_seconds=2, warmup_windows=3, baseline_windows=10, cooldown_seconds=10)
        self.d = SpikeDetector(cfg)

    def messages(self, second, users, repeats=1, prefix="u"):
        hits = []
        for i in range(users):
            for r in range(repeats):
                hits += self.d.feed(ChatMessage(second + 0.01, prefix + str(i), "wow", f"{second}-{i}-{r}"))
        return hits

    def warmup(self):
        for s in (0, 2, 4):
            self.assertFalse(self.messages(s, 2))

    def test_detects_distinct_viewer_spike_and_cooldown(self):
        self.warmup()
        self.messages(6, 20)
        hits = self.d.advance(8)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["timestamp"], 8)
        self.assertEqual(hits[0]["unique_users"], 20)
        self.messages(8, 30)
        self.assertEqual(self.d.advance(10), [])

    def test_one_user_spam_and_bots_do_not_trigger(self):
        self.warmup()
        self.messages(6, 1, 200)
        for i in range(100):
            self.d.feed(ChatMessage(6.1, "Nightbot", "spam", f"bot-{i}"))
        self.assertEqual(self.d.advance(8), [])

    def test_duplicate_ids_are_counted_once(self):
        self.warmup()
        for i in range(100):
            self.d.feed(ChatMessage(6.1, str(i), "wow", "duplicate"))
        self.assertEqual(self.d.advance(8), [])

    def test_long_disconnect_requires_new_warmup(self):
        self.warmup()
        self.messages(100, 30)
        self.assertEqual(self.d.advance(102), [])

    def test_irc_uses_arrival_clock_and_preserves_server_clock(self):
        name, message = parse_irc("@id=a;tmi-sent-ts=12000 :viewer!viewer@host PRIVMSG #test :hi : there", 17)
        self.assertEqual(
            (name, message.timestamp, message.server_timestamp, message.text), ("test", 17, 12, "hi : there")
        )
        self.assertIsNone(parse_irc("PING :tmi.twitch.tv"))


class WithStore(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg.update(data_dir=str(self.root), reel_studio=str(self.root / "studio"))
        self.store = Store(self.root)
        self.file = self.root / "video.mp4"
        self.file.write_bytes(b"controlled test bytes")

    def tearDown(self):
        self.temp.cleanup()

    def add(self, **kwargs):
        return self.store.add("test", self.file, sha256(self.file), **kwargs)


class StoreTests(WithStore):
    def test_concurrent_claims_get_one_owner(self):
        self.add()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.claim("captured", "preparing"), range(8)))
        self.assertEqual(sum(r is not None for r in results), 1)

    def test_same_bytes_and_overlapping_windows_deduplicate(self):
        first = self.add(session_id="s", start=0, end=40)
        self.assertEqual(self.add(), first)
        second = self.store.add("test", self.file, "different", session_id="s", start=5, end=45)
        self.assertEqual(first, second)
        third = self.store.add("test", self.file, "third", session_id="s", start=100, end=140)
        self.assertNotEqual(first, third)

    def test_expired_editorial_lease_recovers_but_publish_does_not(self):
        cid = self.add()
        self.store.claim("captured", "preparing", lease_seconds=-1)
        self.store.recover()
        self.assertEqual(self.store.get(cid)["status"], "captured")
        self.store.update(cid, status="publishing", lease_until=time.time() - 1)
        self.store.recover()
        self.assertEqual(self.store.get(cid)["status"], "publishing")

    def test_stale_editor_cannot_overwrite_another_state(self):
        cid = self.add()
        self.store.update(cid, status="rejected")
        with self.assertRaises(RelayError):
            self.store.update(cid, expected="captured", status="selected")


class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {"duration": 20, "assets": []}

    def test_bounds_and_nonfinite_values_rejected(self):
        for key, value in [("end", 21), ("start", float("nan")), ("zoom", 8)]:
            data = decision()
            data["keep"][0][key] = value
            with self.assertRaises(RelayError):
                validate_decision(data, self.evidence)

    def test_invented_asset_rejected(self):
        data = decision()
        data["sfx"] = [
            {"asset_path": "../../bad.mp3", "at": 1, "duration": 1, "gain_db": -15, "reason": "no"}
        ]
        with self.assertRaises(RelayError):
            validate_decision(data, self.evidence)

    def test_split_requires_actual_in_bounds_rectangle(self):
        data = decision()
        data.update(layout="split", facecam={"x": 0.9, "y": 0.1, "width": 0.2, "height": 0.2})
        with self.assertRaises(RelayError):
            validate_decision(data, self.evidence)

    def test_review_cannot_claim_listening_or_ignore_issues(self):
        data = {
            "approved": True,
            "confidence": 0.9,
            "issues": [],
            "notes": "Frames inspected",
            "audio_review": "not_listened",
        }
        validate_review(data)
        for changes in ({"audio_review": "listened"}, {"issues": ["subject cropped out"]}):
            with self.assertRaises(RelayError):
                validate_review({**data, **changes})


class FakeInstagram:
    def __init__(self):
        self.token = {"account_id": "123", "username": "testbrand"}
        self.creates = self.publishes = 0
        self.remote_status = "FINISHED"
        self.fail_publish = self.fail_permalink = False

    def limit(self):
        return {"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]}

    def create(self, *_):
        self.creates += 1
        return "container-1"

    def status(self, *_):
        return {"status_code": self.remote_status}

    def publish(self, *_):
        self.publishes += 1
        if self.fail_publish:
            raise TimeoutError("Response lost after sending POST")
        return "media-1"

    def permalink(self, *_):
        if self.fail_permalink:
            raise TimeoutError()
        return "https://www.instagram.com/reel/test/"


class PublishingTests(WithStore):
    def setUp(self):
        super().setUp()
        self.cfg["publish"].update(
            enabled=True, dry_run=False, min_interval_seconds=0, expected_username="testbrand"
        )
        self.cid = self.add()
        self.store.update(
            self.cid,
            status="approved",
            review={"approved": True},
            output_path=str(self.file),
            output_hash=sha256(self.file),
            caption="A test",
            public_url="https://media.example.test/reel.mp4",
        )
        self.client = FakeInstagram()

    def test_success_is_idempotent_even_if_permalink_lookup_fails(self):
        self.client.fail_permalink = True
        for _ in range(2):
            result = publish_one(self.cfg, self.cid, client=self.client)
            self.assertEqual(result["status"], "published")
        self.assertEqual((self.client.creates, self.client.publishes), (1, 1))

    def test_uncertain_publish_never_blindly_retries(self):
        self.client.fail_publish = True
        with self.assertRaises(TimeoutError):
            publish_one(self.cfg, self.cid, client=self.client)
        with self.assertRaises(RelayError):
            publish_one(self.cfg, self.cid, client=self.client)
        self.assertEqual(self.client.publishes, 1)
        self.assertEqual(reconcile(self.cfg, self.cid, client=self.client)["status"], "publish_unknown")
        self.client.remote_status = "PUBLISHED"
        self.assertEqual(reconcile(self.cfg, self.cid, client=self.client)["status"], "published")

    def test_container_timeout_resumes_same_container(self):
        result = publish_one(self.cfg, self.cid, client=self.client, processing_timeout=0)
        self.assertIsNone(result)
        self.assertEqual(self.store.get(self.cid)["container_id"], "container-1")
        publish_one(self.cfg, self.cid, client=self.client)
        self.assertEqual((self.client.creates, self.client.publishes), (1, 1))

    def test_changed_export_and_unreviewed_video_are_blocked(self):
        self.file.write_bytes(b"changed after review")
        with self.assertRaises(RelayError):
            publish_one(self.cfg, self.cid, client=self.client)
        self.store.update(self.cid, review=None)
        with self.assertRaises(RelayError):
            publish_one(self.cfg, self.cid, client=self.client)
        self.assertEqual(self.client.creates, 0)

    def test_fixtures_cannot_publish(self):
        with self.store.connect() as db:
            db.execute(schema.candidates.update().where(schema.candidates.c.id == self.cid).values(is_test=1))
        with self.assertRaises(RelayError):
            publish_one(self.cfg, self.cid, client=self.client)

    def test_rolling_day_limit_holds_new_post(self):
        self.cfg["publish"]["max_per_day"] = 1
        previous = self.store.add("test", self.file, "previous")
        self.store.update(previous, status="published", published_at=time.time())
        self.assertIsNone(publish_one(self.cfg, self.cid, client=self.client))
        self.assertEqual(self.client.creates, 0)

    def test_preview_mode_never_contacts_instagram(self):
        self.cfg["publish"]["dry_run"] = True
        with (
            patch("reelrelay.publish.Instagram", side_effect=AssertionError("No API client expected")),
            self.assertRaises(RelayError),
        ):
            publish_one(self.cfg, self.cid)

    def test_different_account_cannot_publish_existing_candidate(self):
        self.store.update(self.cid, publishing_account_id="another-account")
        with self.assertRaises(RelayError):
            publish_one(self.cfg, self.cid, client=self.client)
        self.assertEqual((self.client.creates, self.client.publishes), (0, 0))

    def test_wrong_username_stops_before_media_creation(self):
        self.cfg["publish"]["expected_username"] = "anotherbrand"
        with self.assertRaises(RelayError):
            publish_one(self.cfg, self.cid, client=self.client)
        self.assertEqual(self.client.creates, 0)

    def test_lost_publisher_lock_cannot_send_final_post(self):
        def ready_after_losing_ownership(*_):
            with self.store.connect(write=True) as connection:
                connection.execute(schema.locks.delete())
            return {"status_code": "FINISHED"}

        self.client.status = ready_after_losing_ownership
        with self.assertRaises(LeaseLost):
            publish_one(self.cfg, self.cid, client=self.client)
        self.assertEqual(self.client.publishes, 0)

    def test_pause_during_processing_stops_before_final_post(self):
        from reelrelay.config import save

        path = self.root / "config.toml"
        save(path, self.cfg)
        self.cfg["config_path"] = str(path)

        def ready_after_pause(*_):
            paused = copy.deepcopy(self.cfg)
            paused["publish"]["enabled"] = False
            save(path, paused)
            return {"status_code": "FINISHED"}

        self.client.status = ready_after_pause
        with self.assertRaises(RelayError):
            publish_one(self.cfg, self.cid, client=self.client)
        self.assertEqual(self.client.publishes, 0)


class ServerTests(WithStore):
    def setUp(self):
        super().setUp()
        self.cid = self.add()
        self.url = "http://127.0.0.1:8765"
        self.client = TestClient(create_app(self.cfg), base_url=self.url)

    def tearDown(self):
        self.client.close()
        super().tearDown()

    def test_state_excludes_private_paths(self):
        state = self.client.get("/api/state").json()
        self.assertNotIn("source_path", state["candidates"][0])
        self.assertNotIn("public_url", state["candidates"][0])

    def test_media_range_and_path_traversal(self):
        response = self.client.get(f"/media/{self.cid}/source", headers={"Range": "bytes=0-3"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b"cont")
        self.assertEqual(self.client.get("/../../config.toml").status_code, 404)

    def test_cross_origin_mutations_are_denied_and_local_action_works(self):
        token = self.client.get("/api/state").json()["settings"]["csrf"]
        for origin in ("https://attacker.example", self.url):
            response = self.client.post(
                "/api/reject",
                json={"id": self.cid, "version": self.store.get(self.cid)["updated_at"]},
                headers={"Origin": origin, "X-Relay-Token": token},
            )
            if origin != self.url:
                self.assertEqual(response.status_code, 403)
            else:
                self.assertTrue(response.json()["ok"])
        self.assertEqual(self.store.get(self.cid)["status"], "rejected")

    def test_rebinding_host_is_rejected(self):
        response = self.client.get("/api/state", headers={"Host": "attacker.example"})
        self.assertEqual(response.status_code, 403)

    def test_health_and_openapi(self):
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        self.assertIn("/api/approve", self.client.get("/openapi.json").json()["paths"])


if __name__ == "__main__":
    unittest.main()
