import concurrent.futures
import copy
import tempfile
import time
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pydantic import ValidationError

from reelrelay import db
from reelrelay.config import DEFAULTS, Settings
from reelrelay.credentials import Vault
from reelrelay.delivery import delivery_url, resolve_delivery
from reelrelay.oauth import OAuthStates, instagram_callback, instagram_start, instagram_token
from reelrelay.store import LeaseLost, Store
from reelrelay.util import RelayError


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = self.temp.name
        self.store = Store(self.cfg)

    def tearDown(self):
        self.temp.cleanup()

    def test_old_worker_cannot_overwrite_reclaimed_job(self):
        cid = self.store.add("test", "test.mp4", "hash")
        old = self.store.claim("captured", "preparing", lease_seconds=-1)
        self.store.recover()
        new = self.store.claim("captured", "preparing")
        self.assertNotEqual(old["claim_token"], new["claim_token"])
        with self.assertRaises(LeaseLost):
            self.store.update(cid, expected="preparing", claim_token=old["claim_token"], status="needs_agent")
        self.store.update(cid, expected="preparing", claim_token=new["claim_token"], status="needs_agent")

    def test_worker_heartbeat_keeps_job_owned(self):
        cid = self.store.add("test", "test.mp4", "hash")
        item = self.store.claim("captured", "preparing", lease_seconds=0.5)
        with self.store.lease(item, seconds=0.5):
            time.sleep(0.7)
            self.store.recover()
            self.assertEqual(self.store.get(cid)["status"], "preparing")

    def test_delay_is_respected(self):
        cid = self.store.add("test", "test.mp4", "hash")
        self.store.update(cid, available_at=time.time() + 100)
        self.assertIsNone(self.store.claim("captured", "preparing"))

    def test_recorder_losing_ownership_cannot_enqueue_more_clips(self):
        with self.store.lock("capture:test") as guard:
            cid = self.store.add("test", "one.mp4", "hash-one", guard=guard)
            with self.store.connect(write=True) as connection:
                connection.execute(
                    db.locks.update().where(db.locks.c.name == guard.name).values(owner="replacement")
                )
            with self.assertRaises(LeaseLost):
                self.store.add("test", "two.mp4", "hash-two", guard=guard)
            with self.assertRaises(LeaseLost):
                self.store.update(cid, guard=guard, status="rejected")
            self.assertEqual(len(self.store.list()), 1)
            self.assertEqual(self.store.get(cid)["status"], "captured")

    def test_agent_budget_is_atomic_across_threads(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda n: self.store.reserve_agent_call(3, str(n), "select"), range(24)))
        self.assertEqual(sum(results), 3)

    def test_configuration_rejects_invalid_limits_and_duplicates(self):
        for section, key, value in [
            ("agent", "max_revisions", -1),
            ("detector", "ratio", float("nan")),
            ("publish", "enabled", "true"),
            ("capture", "reconnect_seconds", 0),
        ]:
            config = copy.deepcopy(DEFAULTS)
            config[section][key] = value
            with self.assertRaises(ValidationError):
                Settings.model_validate(config)
        config = copy.deepcopy(DEFAULTS)
        config["channels"] = [{"name": "hello"}, {"name": "Hello"}]
        with self.assertRaises(ValidationError):
            Settings.model_validate(config)

    def test_unversioned_prototype_migrates_without_losing_candidates(self):
        root = Path(self.temp.name) / "legacy"
        root.mkdir()
        engine = db.engine_for(db.url_for(root))
        with engine.begin() as connection:
            migration = Config()
            migration.set_main_option("script_location", str(Path(db.__file__).parent / "migrations"))
            migration.attributes["connection"] = connection
            command.upgrade(migration, "0001")
            connection.execute(
                db.candidates.insert().values(
                    id="legacy-candidate",
                    channel="test",
                    status="captured",
                    created_at=1,
                    updated_at=1,
                    source_path="original.mp4",
                    source_hash="old-hash",
                )
            )
            connection.exec_driver_sql("DROP TABLE alembic_version")
        migrated = Store(root)
        self.assertEqual(migrated.get("legacy-candidate")["source_hash"], "old-hash")
        self.assertEqual(migrated.get("legacy-candidate")["available_at"], 0)
        with migrated.connect() as connection:
            self.assertEqual(
                connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one(), "0002"
            )

    def test_unknown_legacy_database_is_not_modified(self):
        root = Path(self.temp.name) / "unrecognized"
        root.mkdir()
        engine = db.engine_for(db.url_for(root))
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE candidates (id TEXT, source_hash TEXT, metadata TEXT, status TEXT, created_at REAL)"
            )
        with self.assertRaises(RelayError):
            Store(root)
        with engine.connect() as connection:
            self.assertFalse(sa.inspect(connection).has_table("alembic_version"))

    def test_vault_encrypts_tokens_and_limits_public_status(self):
        vault = Vault(self.cfg)
        vault.put(
            "instagram",
            {"access_token": "secret-test-token", "username": "separatebrand", "account_id": "123"},
        )
        self.assertEqual(vault.get("instagram")["access_token"], "secret-test-token")
        with self.store.connect() as c:
            encrypted = c.execute(sa.select(db.vault.c.ciphertext)).scalar_one()
        self.assertNotIn("secret-test-token", encrypted)
        self.assertNotIn("access_token", vault.account_status("instagram"))
        self.assertEqual((Path(self.temp.name) / ".private/master.key").stat().st_mode & 0o777, 0o600)

    def test_oauth_state_is_browser_bound_expires_and_is_single_use(self):
        states = OAuthStates(self.cfg)
        state = states.begin("instagram", "browser-one", {"redirect_uri": "https://example.test/callback"})
        with self.assertRaises(RelayError):
            states.consume("instagram", state, "different-browser")
        self.assertIn("redirect_uri", states.consume("instagram", state, "browser-one"))
        with self.assertRaises(RelayError):
            states.consume("instagram", state, "browser-one")
        expired = states.begin("instagram", "browser-one", ttl=-1)
        with self.assertRaises(RelayError):
            states.consume("instagram", expired, "browser-one")

    def test_instagram_oauth_and_refresh_with_simulated_service(self):
        self.cfg["auth"].update(
            instagram_app_id="987",
            instagram_redirect_uri="https://relay.example.test/auth/instagram/callback",
        )
        vault = Vault(self.cfg)
        vault.put("instagram-app", {"client_secret": "app-secret"})
        from urllib.parse import parse_qs, urlparse

        url = instagram_start(self.cfg, "browser")
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query["enable_fb_login"], ["false"])
        calls = []

        def request(url, **kwargs):
            calls.append((url, kwargs))
            if "api.instagram.com/oauth" in url:
                return {
                    "data": [
                        {
                            "access_token": "short",
                            "user_id": "123",
                            "permissions": "instagram_business_basic,instagram_business_content_publish",
                        }
                    ]
                }
            if "/access_token?" in url:
                return {"access_token": "long", "expires_in": 60 * 86400}
            return {"user_id": "123", "username": "separatebrand"}

        result = instagram_callback(
            self.cfg, state=query["state"][0], binding="browser", code="code", request=request
        )
        self.assertEqual(result["username"], "separatebrand")
        self.assertEqual(len(calls), 3)
        token = vault.get("instagram")
        token.update(obtained_at=time.time() - 2 * 86400, expires_at=time.time() + 86400)
        vault.put("instagram", token)
        refreshed = instagram_token(
            self.cfg, request=lambda *a, **k: {"access_token": "renewed", "expires_in": 60 * 86400}
        )
        self.assertEqual(refreshed["access_token"], "renewed")

    def test_oauth_denial_does_not_replace_account(self):
        vault = Vault(self.cfg)
        vault.put("instagram", {"access_token": "old-token", "username": "previous"})
        state = OAuthStates(self.cfg).begin("instagram", "browser")
        with self.assertRaises(RelayError):
            instagram_callback(self.cfg, state=state, binding="browser", error="access_denied")
        self.assertEqual(vault.get("instagram")["username"], "previous")

    def test_signed_delivery_rejects_tampering_expiry_and_changed_export(self):
        from urllib.parse import parse_qs, urlparse

        from reelrelay.util import sha256

        self.cfg["web"]["public_base_url"] = "https://relay.example.test"
        path = Path(self.temp.name) / "export.mp4"
        path.write_bytes(b"approved video")
        cid = self.store.add("test", path, sha256(path))
        self.store.update(
            cid, output_path=str(path), output_hash=sha256(path), review={"approved": True}, status="approved"
        )
        url = delivery_url(self.cfg, self.store.get(cid))
        query = parse_qs(urlparse(url).query)
        expiry, signature = int(query["expires"][0]), query["signature"][0]
        self.assertEqual(resolve_delivery(self.cfg, cid, expiry, signature), path.resolve())
        with self.assertRaises(RelayError):
            resolve_delivery(self.cfg, cid, expiry, "wrong-signature")
        with self.assertRaises(RelayError):
            resolve_delivery(self.cfg, cid, int(time.time() - 1), signature)
        path.write_bytes(b"changed after review")
        with self.assertRaises(RelayError):
            resolve_delivery(self.cfg, cid, expiry, signature)


if __name__ == "__main__":
    unittest.main()
