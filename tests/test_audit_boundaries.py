"""Regression checks for observed configuration, queue and import failures."""

import copy
import multiprocessing
import tempfile
import threading
import time
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from reelrelay import db
from reelrelay.cli import import_file
from reelrelay.config import DEFAULTS, Settings, load, load_versioned, mutate, save
from reelrelay.publish import (
    AccountUnavailable,
    publish_one,
    publish_work,
    reconcile,
    retry_failed_publication,
)
from reelrelay.store import LeaseLost, LockBusy, Store
from reelrelay.util import APIError, RelayError, sha256


def _save_preferences(path, entered, release):
    def change(cfg):
        cfg["publish"]["max_per_day"] = 7
        entered.set()
        if not release.wait(10):
            raise RuntimeError("Test did not release settings writer")

    mutate(path, change)


def _pause_publishing(path, started=None, entered=None):
    def change(cfg):
        if entered is not None:
            entered.set()
        cfg["publish"].update(enabled=False, dry_run=True)

    if started is not None:
        started.set()
    mutate(path, change)


class ConfigMutationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "config.toml"
        cfg = copy.deepcopy(DEFAULTS)
        cfg["publish"].update(enabled=True, dry_run=False)
        save(self.path, cfg)

    def tearDown(self):
        self.temp.cleanup()

    def test_concurrent_process_settings_save_cannot_undo_pause(self):
        ctx = multiprocessing.get_context("spawn")
        entered, release, pause_started, pause_entered = (ctx.Event() for _ in range(4))
        preferences = ctx.Process(target=_save_preferences, args=(self.path, entered, release))
        pause = ctx.Process(target=_pause_publishing, args=(self.path, pause_started, pause_entered))
        preferences.start()
        try:
            self.assertTrue(entered.wait(10))
            pause.start()
            self.assertTrue(pause_started.wait(10))
            self.assertFalse(pause_entered.wait(0.1), "Second process entered the locked mutation")
            release.set()
            for process in (preferences, pause):
                process.join(10)
                self.assertEqual(process.exitcode, 0)
        finally:
            release.set()
            for process in (preferences, pause):
                if process.pid and process.is_alive():
                    process.terminate()
                    process.join(5)
        cfg = load(self.path)
        self.assertFalse(cfg["publish"]["enabled"])
        self.assertTrue(cfg["publish"]["dry_run"])
        self.assertEqual(cfg["publish"]["max_per_day"], 7)

    def test_stale_whole_file_save_and_mutation_are_rejected(self):
        old, version = load_versioned(self.path)
        _pause_publishing(self.path)
        with self.assertRaises(RelayError):
            save(self.path, old, expected_version=version)
        with self.assertRaises(RelayError):
            mutate(self.path, lambda cfg: cfg["publish"].update(enabled=True), expected_version=version)
        self.assertFalse(load(self.path)["publish"]["enabled"])

    def test_invalid_mutation_preserves_the_previous_file(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValidationError):
            mutate(self.path, lambda cfg: cfg["publish"].update(max_per_day=-1))
        self.assertEqual(self.path.read_bytes(), before)

    def test_custom_sqlite_locations_are_rejected_by_config_and_store(self):
        urls = ["sqlite:///custom.db", "sqlite+pysqlite:///:memory:", "sqlite:///nested/control.sqlite3"]
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    Settings.model_validate({"database_url": url})
                with self.assertRaises(RelayError):
                    db.url_for(self.path.parent, url)

    def test_environment_override_cannot_move_sqlite_outside_the_protected_location(self):
        target = self.path.parent / "unprotected.db"
        with (
            patch.dict("os.environ", {"REEL_RELAY_DATABASE_URL": "sqlite:///" + str(target)}),
            self.assertRaises(RelayError),
        ):
            load(self.path)
        self.assertFalse(target.exists())

    def test_default_sqlite_and_postgres_remain_supported_without_exposing_bad_urls(self):
        with patch.dict("os.environ", {"REEL_RELAY_DATABASE_URL": ""}):
            local = db.url_for(self.path.parent)
        self.assertEqual(Path(local.database), self.path.parent.resolve() / "queue.sqlite3")
        postgres = "postgresql://synthetic:synthetic-password@localhost/relay_test"
        self.assertEqual(db.url_for(self.path.parent, postgres).drivername, "postgresql+psycopg")
        self.assertEqual(Settings.model_validate({"database_url": postgres}).database_url, postgres)
        with self.assertRaises(ValidationError) as caught:
            Settings.model_validate({"database_url": "mysql://synthetic:synthetic-password@localhost/db"})
        self.assertNotIn("synthetic-password", str(caught.exception))

    def test_unrelated_mutation_preserves_relative_paths_when_workspace_moves(self):
        mutate(self.path, lambda cfg: cfg["publish"].update(enabled=False))
        raw = tomllib.loads(self.path.read_text())
        self.assertEqual(raw["data_dir"], "runtime")
        self.assertEqual(raw["reel_studio"], "..")
        moved = self.path.parent / "moved" / "config.toml"
        moved.parent.mkdir()
        moved.write_bytes(self.path.read_bytes())
        self.assertEqual(load(moved)["data_dir"], str(moved.parent.resolve() / "runtime"))

    def test_intentional_absolute_path_change_is_preserved(self):
        absolute = str(self.path.parent.resolve() / "explicit-media")
        mutate(self.path, lambda cfg: cfg.update(data_dir=absolute))
        self.assertEqual(tomllib.loads(self.path.read_text())["data_dir"], absolute)


class ImportAndPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root / "data")
        self.source = self.root / "selected.mp4"
        self.source.write_bytes(b"synthetic import fixture")
        self.store = Store(self.cfg)

    def tearDown(self):
        self.temp.cleanup()

    def test_duplicate_import_keeps_only_the_referenced_copy(self):
        with patch("reelrelay.cli.media.probe", return_value={"duration": 10}):
            ids = [import_file(self.cfg, self.source) for _ in range(3)]
        self.assertEqual(len(set(ids)), 1)
        copies = list((self.root / "data/sources").iterdir())
        self.assertEqual(copies, [Path(self.store.get(ids[0])["source_path"])])
        self.assertEqual(self.source.read_bytes(), b"synthetic import fixture")

    def test_failed_probe_and_interrupted_copy_remove_only_the_new_copy(self):
        with (
            patch("reelrelay.cli.media.probe", side_effect=RelayError("invalid media")),
            self.assertRaises(RelayError),
        ):
            import_file(self.cfg, self.source)

        def interrupted_copy(_, target):
            target.write_bytes(b"incomplete")
            raise OSError("disk interrupted")

        with patch("reelrelay.cli.shutil.copyfile", side_effect=interrupted_copy), self.assertRaises(OSError):
            import_file(self.cfg, self.source)
        self.assertEqual(list((self.root / "data/sources").iterdir()), [])
        self.assertTrue(self.source.exists())

    def test_ambiguous_database_commit_never_deletes_a_referenced_import(self):
        add = Store.add

        def committed_then_disconnected(store, *args, **kwargs):
            add(store, *args, **kwargs)
            raise OSError("connection lost after commit")

        with (
            patch("reelrelay.cli.media.probe", return_value={"duration": 10}),
            patch.object(Store, "add", committed_then_disconnected),
            self.assertRaises(OSError),
        ):
            import_file(self.cfg, self.source)
        self.assertTrue(Path(self.store.list()[0]["source_path"]).is_file())

    def _approved(self, label="oldest"):
        output = self.root / "data" / (label + ".mp4")
        output.write_bytes(label.encode())
        cid = self.store.add("test", output, sha256(output))
        self.store.update(
            cid,
            status="approved",
            output_path=str(output),
            output_hash=sha256(output),
            review={"approved": True},
            public_url="https://example.test/fixture.mp4",
        )
        self.cfg["publish"].update(
            enabled=True, dry_run=False, expected_username="testbrand", min_interval_seconds=0
        )
        return cid

    def test_publisher_finds_approved_export_behind_more_than_1000_newer_rows(self):
        cid = self._approved()
        now = time.time()
        with self.store.connect(write=True) as connection:
            connection.execute(
                db.candidates.insert(),
                [
                    {
                        "id": f"{index:016x}",
                        "channel": "test",
                        "status": "rejected",
                        "created_at": now + index,
                        "updated_at": now + index,
                    }
                    for index in range(1001)
                ],
            )
        client = LocalInstagram()
        with patch("reelrelay.publish.Instagram", return_value=client):
            publish_work(self.cfg, threading.Event())
        self.assertEqual(self.store.get(cid)["status"], "published")
        self.assertEqual(client.publishes, 1)

    def test_automatic_selection_skips_future_and_unknown_jobs_and_resumes_container(self):
        delayed = self._approved("delayed")
        unknown = self._approved("unknown")
        due = self._approved("due")
        self.store.update(delayed, available_at=time.time() + 3600)
        self.store.update(unknown, status="publish_unknown", container_id="unknown-container")
        self.store.update(due, status="container_ready", container_id="existing-container")
        client = LocalInstagram()
        result = publish_one(self.cfg, client=client)
        self.assertEqual(result["id"], due)
        self.assertEqual(client.created, 0)
        self.assertEqual(client.publishes, 1)
        self.assertEqual(self.store.get(unknown)["status"], "publish_unknown")

    def test_lost_publish_response_reserves_daily_budget_and_reconciliation_counts_once(self):
        first = self._approved("accepted-unknown")
        second = self._approved("waiting")
        self.cfg["publish"]["max_per_day"] = 1
        client = LocalInstagram()
        with (
            patch.object(client, "publish", side_effect=TimeoutError("accepted but response lost")),
            self.assertRaises(TimeoutError),
        ):
            publish_one(self.cfg, first, client=client)
        self.assertEqual(self.store.get(first)["status"], "publish_unknown")
        self.assertEqual(self.store.publication_window()[0], 1)
        self.assertIsNone(publish_one(self.cfg, second, client=client))
        self.assertEqual(client.publishes, 0)
        with patch.object(client, "status", return_value={"status_code": "PUBLISHED"}):
            reconcile(self.cfg, first, client=client)
        self.assertEqual(self.store.publication_window()[0], 1)

    def test_final_send_reservation_also_holds_the_minimum_interval(self):
        first = self._approved("pending-send")
        second = self._approved("waiting")
        self.cfg["publish"]["min_interval_seconds"] = 1800
        self.store.update(first, status="container_ready", container_id="pending-container")
        with self.store.lock("instagram-publisher") as guard:
            self.store.reserve_publication(first, guard)
        self.assertEqual(self.store.get(first)["status"], "publishing")
        client = LocalInstagram()
        self.assertIsNone(publish_one(self.cfg, second, client=client))
        self.assertEqual(client.created, 0)
        self.store.update(first, status="publish_unknown")
        self.assertIsNone(publish_one(self.cfg, second, client=client))
        self.assertEqual(client.created, 0)

    def test_cadence_uses_attempt_time_instead_of_later_error_updates(self):
        cid = self._approved()
        self.store.update(cid, status="container_ready", container_id="old-container")
        with (
            self.store.lock("instagram-publisher") as guard,
            patch("reelrelay.store.time.time", return_value=time.time() - 90000),
        ):
            self.store.reserve_publication(cid, guard)
        self.store.update(cid, status="publish_unknown", error="Rechecked old uncertainty")
        self.assertEqual(self.store.publication_window(), (0, None))

    def test_container_creation_failure_does_not_consume_cadence_or_prevent_safe_retry(self):
        cid = self._approved()
        self.cfg["publish"]["max_per_day"] = 1
        client = LocalInstagram()
        with (
            patch.object(client, "create", side_effect=TimeoutError("container response lost")),
            self.assertRaises(TimeoutError),
        ):
            publish_one(self.cfg, cid, client=client)
        self.assertEqual(self.store.get(cid)["status"], "uploading")
        self.assertEqual(self.store.publication_window(), (0, None))
        result = publish_one(self.cfg, cid, client=client)
        self.assertEqual(result["status"], "published")
        self.assertEqual(client.publishes, 1)
        self.assertEqual(self.store.publication_window()[0], 1)

    def test_account_and_quota_errors_preserve_all_approved_candidates(self):
        first, second = self._approved("first"), self._approved("second")
        error = APIError(400, {"error": {"code": 190, "message": "synthetic expired session"}})
        with patch("reelrelay.publish.Instagram", side_effect=error):
            for _ in range(2):
                with self.assertRaises(AccountUnavailable):
                    publish_work(self.cfg, threading.Event())
        client = LocalInstagram()
        with (
            patch("reelrelay.publish.Instagram", return_value=client),
            patch.object(client, "limit", side_effect=error),
            self.assertRaises(AccountUnavailable),
        ):
            publish_work(self.cfg, threading.Event())
        for cid in (first, second):
            item = self.store.get(cid)
            self.assertEqual(item["status"], "approved")
            self.assertNotIn("publication_errors", item["metadata"])
            self.assertEqual(item["available_at"], 0)

    def test_http_status_alone_does_not_permanently_invalidate_media(self):
        cid, client = self._approved(), LocalInstagram()
        with (
            patch("reelrelay.publish.Instagram", return_value=client),
            patch.object(client, "create", side_effect=APIError(400, {"error": {"code": 190}})),
            self.assertRaises(APIError),
        ):
            publish_work(self.cfg, threading.Event())
        self.assertEqual(self.store.get(cid)["status"], "uploading")
        self.assertGreater(self.store.get(cid)["available_at"], time.time())
        with (
            patch.object(client, "status", return_value={"status_code": "ERROR"}),
            self.assertRaises(RelayError),
        ):
            publish_one(self.cfg, cid, client=client)
        self.assertEqual(self.store.get(cid)["status"], "publish_failed")

    def test_lock_contention_does_not_change_the_candidate(self):
        cid = self._approved()
        before = self.store.get(cid)
        with self.store.lock("instagram-publisher"), self.assertRaises(LockBusy):
            publish_work(self.cfg, threading.Event())
        self.assertEqual(self.store.get(cid), before)

    def test_watch_loop_survives_secondary_error_recording_failure(self):
        cid, stop = self._approved(), threading.Event()
        with (
            patch("reelrelay.publish.publish_one", side_effect=APIError(500, {})),
            patch.object(Store, "event", side_effect=OSError("synthetic database unavailable")),
            patch.object(stop, "wait", side_effect=lambda seconds: stop.set()),
        ):
            publish_work(self.cfg, stop, watch=True)
        self.assertTrue(stop.is_set())
        self.assertEqual(self.store.get(cid)["status"], "approved")

    def test_polling_revalidates_review_and_caption_before_final_send(self):
        for label, changes in (
            ("revoked", {"review": {"approved": False}}),
            ("recaptioned", {"caption": "changed while processing"}),
        ):
            with self.subTest(change=label):
                cid, client = self._approved(label), LocalInstagram()

                def changed_while_processing(*_, cid=cid, changes=changes):
                    self.store.update(cid, **changes)
                    return {"status_code": "FINISHED"}

                with (
                    patch.object(client, "status", side_effect=changed_while_processing),
                    self.assertRaises(RelayError),
                ):
                    publish_one(self.cfg, cid, client=client)
                self.assertEqual(client.publishes, 0)
        self.assertEqual(self.store.publication_window(), (0, None))

    def test_final_send_reservation_rejects_a_stale_row_version(self):
        cid = self._approved()
        self.store.update(cid, status="container_ready", container_id="test-container")
        version = self.store.get(cid)["updated_at"]
        self.store.update(cid, review={"approved": False})
        with self.store.lock("instagram-publisher") as guard, self.assertRaises(LeaseLost):
            self.store.reserve_publication(cid, guard, expected_version=version)
        self.assertEqual(self.store.get(cid)["status"], "container_ready")
        self.assertEqual(self.store.publication_window(), (0, None))

    def _processing_failure(self, label="failed", status="ERROR"):
        cid, client = self._approved(label), LocalInstagram()
        with (
            patch.object(client, "status", return_value={"status_code": status}),
            self.assertRaises(RelayError),
        ):
            publish_one(self.cfg, cid, client=client)
        self.assertEqual(client.publishes, 0)
        return cid, client

    def test_explicit_retry_replaces_failed_container_and_preserves_pause_and_approval(self):
        for remote_status in ("ERROR", "EXPIRED"):
            with self.subTest(remote_status=remote_status):
                cid, client = self._processing_failure(remote_status, remote_status)
                self.store.update(cid, available_at=time.time() + 900)
                before = self.store.get(cid)
                self.cfg["publish"].update(enabled=False, dry_run=True)
                after = retry_failed_publication(self.cfg, cid, before["updated_at"])
                self.assertEqual(after["status"], "approved")
                self.assertIsNone(after["container_id"])
                self.assertIsNone(after["public_url"])
                self.assertIsNone(after["error"])
                self.assertEqual(after["available_at"], 0)
                self.assertEqual(after["review"], before["review"])
                self.assertEqual(after["output_hash"], before["output_hash"])
                self.assertEqual(after["publishing_account_id"], before["publishing_account_id"])
                self.assertNotIn("publication_failure", after["metadata"])
                self.assertFalse(self.cfg["publish"]["enabled"])
                self.assertTrue(self.cfg["publish"]["dry_run"])
                events = [
                    event
                    for event in self.store.snapshot()["events"]
                    if event["candidate_id"] == cid and event["kind"] == "publication_retry_requested"
                ]
                self.assertEqual(len(events), 1)
                self.cfg["publish"].update(enabled=True, dry_run=False)
                with (
                    patch("reelrelay.publish.public_url", return_value="https://example.test/retry.mp4"),
                    patch.object(client, "create", return_value="replacement-container") as create,
                ):
                    result = publish_one(self.cfg, cid, client=client)
                create.assert_called_once()
                self.assertEqual(result["container_id"], "replacement-container")
                self.assertEqual(result["status"], "published")
                self.assertEqual(client.publishes, 1)

    def test_publication_retry_rejects_unknown_legacy_or_prior_final_attempt(self):
        for variant in ("publishing", "publish_unknown", "legacy", "prior-attempt", "published"):
            with self.subTest(variant=variant):
                cid, _ = self._processing_failure(variant)
                if variant in {"publishing", "publish_unknown"}:
                    self.store.update(cid, status=variant)
                elif variant == "legacy":
                    self.store.update(cid, metadata={})
                elif variant == "prior-attempt":
                    self.store.event("publication_attempt", {}, cid)
                else:
                    self.store.update(cid, media_id="already-sent", published_at=time.time())
                before = self.store.get(cid)
                with self.assertRaises(RelayError):
                    retry_failed_publication(self.cfg, cid, before["updated_at"])
                self.assertEqual(self.store.get(cid), before)

    def test_publication_retry_rejects_stale_version_or_modified_export(self):
        cid, _ = self._processing_failure()
        version = self.store.get(cid)["updated_at"]
        self.store.update(cid, error="new processing diagnostic")
        with self.assertRaises(LeaseLost):
            retry_failed_publication(self.cfg, cid, version)
        item = self.store.get(cid)
        Path(item["output_path"]).write_bytes(b"synthetic changed video")
        with self.assertRaisesRegex(RelayError, "changed after review"):
            retry_failed_publication(self.cfg, cid, item["updated_at"])
        self.assertEqual(self.store.get(cid), item)

    def test_publication_retry_cannot_overlap_an_active_publisher(self):
        cid, _ = self._processing_failure()
        before = self.store.get(cid)
        with self.store.lock("instagram-publisher"), self.assertRaises(LockBusy):
            retry_failed_publication(self.cfg, cid, before["updated_at"])
        self.assertEqual(self.store.get(cid), before)


class LocalInstagram:
    def __init__(self):
        self.token = {"account_id": "test-account", "username": "testbrand"}
        self.created = self.publishes = 0

    def limit(self):
        return {"data": [{"quota_usage": 0, "config": {"quota_total": 100}}]}

    def create(self, *_):
        self.created += 1
        return "new-container"

    def status(self, *_):
        return {"status_code": "FINISHED"}

    def publish(self, *_):
        self.publishes += 1
        return "test-media"

    def permalink(self, *_):
        return "https://www.instagram.com/reel/test-media/"
