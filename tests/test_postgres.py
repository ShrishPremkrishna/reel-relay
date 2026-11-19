"""Opt-in integration tests; only a disposable local database named relay_test* is accepted."""

import concurrent.futures
import hashlib
import os
import tempfile
import time
import unittest
import uuid

import sqlalchemy as sa

from reelrelay import db
from reelrelay.store import LeaseLost, Store

TEST_URL = os.environ.get("REEL_RELAY_TEST_DATABASE_URL", "")


def consume(root, url, channel):
    store, completed = Store(root, database_url=url), []
    while True:
        item = store.claim("captured", "preparing")
        if item is None:
            store.engine.dispose()
            return completed
        if item["channel"] != channel:
            raise RuntimeError("Test database contains unrelated queued work")
        time.sleep(0.002)
        store.update(item["id"], expected="preparing", claim_token=item["claim_token"], status="needs_agent")
        completed.append(item["id"])


@unittest.skipUnless(TEST_URL, "Set REEL_RELAY_TEST_DATABASE_URL for disposable PostgreSQL tests")
class PostgreSQLTests(unittest.TestCase):
    def setUp(self):
        url = sa.make_url(TEST_URL)
        if url.host not in ("localhost", "127.0.0.1") or not (url.database or "").startswith("relay_test"):
            self.fail("Refusing to run integration tests outside a disposable local relay_test database")
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.store = Store(self.root, database_url=TEST_URL)
        self.channel = "load_" + uuid.uuid4().hex[:12]

    def tearDown(self):
        with self.store.connect(write=True) as c:
            c.execute(db.candidates.delete().where(db.candidates.c.channel == self.channel))
            c.execute(db.health.delete().where(db.health.c.channel == self.channel))
        self.store.engine.dispose()
        self.temp.cleanup()

    def add(self, index, **kwargs):
        digest = hashlib.sha256(f"{self.channel}-{index}".encode()).hexdigest()
        return self.store.add(self.channel, "test.mp4", digest, **kwargs)

    def test_eight_worker_processes_claim_every_job_once(self):
        ids = {self.add(i) for i in range(240)}
        with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(consume, self.root, TEST_URL, self.channel) for _ in range(8)]
            completed = [cid for future in futures for cid in future.result(timeout=60)]
        self.assertEqual(set(completed), ids)
        self.assertEqual(len(completed), len(ids))

    def test_same_source_and_overlapping_windows_deduplicate_concurrently(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            same = list(pool.map(lambda _: self.add(0), range(24)))
            overlaps = list(
                pool.map(lambda n: self.add(n + 1, session_id=self.channel, start=n, end=n + 75), range(24))
            )
        self.assertEqual(len(set(same)), 1)
        self.assertEqual(len(set(overlaps)), 1)

    def test_health_fields_survive_simultaneous_updates(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda n: self.store.health(self.channel, **{f"field{n}": n}), range(24)))
        row = next(h for h in self.store.snapshot()["channels"] if h["channel"] == self.channel)
        self.assertTrue(all(row[f"field{n}"] == n for n in range(24)))

    def test_postgres_fences_an_expired_worker(self):
        cid = self.add(0)
        old = self.store.claim("captured", "preparing", cid=cid, lease_seconds=-1)
        self.store.recover()
        new = self.store.claim("captured", "preparing", cid=cid)
        with self.assertRaises(LeaseLost):
            self.store.update(cid, expected="preparing", claim_token=old["claim_token"], status="needs_agent")
        self.store.update(cid, expected="preparing", claim_token=new["claim_token"], status="needs_agent")


if __name__ == "__main__":
    unittest.main()
