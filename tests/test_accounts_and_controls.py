import copy
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse

from fastapi.testclient import TestClient

from reelrelay.auth import twitch_device_poll, twitch_device_start
from reelrelay.config import DEFAULTS, load, save
from reelrelay.credentials import Vault
from reelrelay.delivery import delivery_url
from reelrelay.server import create_app
from reelrelay.store import Store
from reelrelay.util import APIError, RelayError, sha256


class AccountControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.path = self.root / "config.toml"
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root / "data")
        save(self.path, self.cfg)
        self.cfg = load(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_environment_database_password_is_not_saved(self):
        with patch.dict(os.environ, {"REEL_RELAY_DATABASE_URL": "postgresql://user:secret@host/db"}):
            config = load(self.path)
            self.assertIn("secret", config["database_url"])
            save(self.path, config)
        self.assertNotIn("secret", self.path.read_text())
        self.assertEqual(load(self.path)["database_url"], "")

    def test_twitch_device_flow_throttles_polling_and_saves_verified_identity(self):
        calls = []
        pending = [True]

        def request(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/device"):
                return {
                    "device_code": "private-device-code",
                    "user_code": "ABCD",
                    "verification_uri": "https://www.twitch.tv/activate",
                    "expires_in": 300,
                    "interval": 5,
                }
            if url.endswith("/token"):
                if pending[0]:
                    pending[0] = False
                    raise APIError(400, {"message": "authorization_pending"})
                return {"access_token": "access", "refresh_token": "refresh", "expires_in": 3600}
            return {
                "client_id": "publicapp",
                "user_id": "42",
                "login": "separatebrand",
                "scopes": ["user:read:chat"],
            }

        device = twitch_device_start(self.cfg, "publicapp", request=request)
        self.assertNotIn("device_code", device)
        self.assertEqual(twitch_device_poll(self.cfg, request=request)["status"], "pending")
        self.assertEqual(len(calls), 1)
        vault = Vault(self.cfg)
        for expected in ("pending", "connected"):
            flow = vault.get("twitch-device")
            flow["next_poll_at"] = 0
            vault.put("twitch-device", flow)
            self.assertEqual(twitch_device_poll(self.cfg, request=request)["status"], expected)
        self.assertEqual(vault.account_status("twitch")["username"], "separatebrand")
        self.assertFalse(vault.exists("twitch-device"))

    def test_expired_twitch_device_does_not_call_service(self):
        Vault(self.cfg).put("twitch-device", {"expires_at": time.time() - 1})
        with self.assertRaises(RelayError):
            twitch_device_poll(self.cfg, request=lambda *a, **k: self.fail("No network call expected"))

    def test_setup_secrets_not_echoed_and_publishing_requires_account(self):
        with TestClient(create_app(self.cfg), base_url="http://127.0.0.1:8765") as client:
            data = client.get("/api/setup").json()
            headers = {"Origin": "http://127.0.0.1:8765", "X-Relay-Token": data["csrf"]}
            update = {
                "channels": [{"name": "examplechannel"}],
                "provider": "manual",
                "instagram_app_id": "123",
                "instagram_app_secret": "private-secret",
            }
            self.assertEqual(client.post("/api/setup", json=update, headers=headers).status_code, 200)
            result = client.get("/api/setup")
            self.assertNotIn("private-secret", result.text)
            self.assertNotIn("private-secret", self.path.read_text())
            self.assertEqual(result.json()["channels"][0]["name"], "examplechannel")
            blocked = client.post(
                "/api/publishing", json={"enabled": True, "expected_username": "example"}, headers=headers
            )
            self.assertEqual(blocked.status_code, 400)
            self.assertTrue(load(self.path)["publish"]["dry_run"])
            for path in ("/setup", "/api/readiness", "/api/assets", "/api/activity"):
                self.assertEqual(client.get(path).status_code, 200)
            import re

            html = client.get("/setup").text
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
            self.assertTrue(assets)
            for path in assets:
                self.assertEqual(client.get(path).status_code, 200)

    def test_other_web_workers_observe_saved_configuration(self):
        with TestClient(create_app(self.cfg), base_url="http://127.0.0.1:8765") as client:
            changed = load(self.path)
            changed["publish"]["max_per_day"] = 37
            save(self.path, changed)
            self.assertEqual(client.get("/api/state").json()["settings"]["daily_limit"], 37)

    def test_remote_auth_and_signed_delivery_work_without_exposing_workspace(self):
        self.cfg["web"]["public_base_url"] = "https://relay.example.test"
        save(self.path, self.cfg)
        with (
            patch.dict(os.environ, {"REEL_RELAY_WEB_TOKEN": "test-server-token-" * 3}),
            TestClient(create_app(self.cfg), base_url="https://relay.example.test") as client,
        ):
            self.assertEqual(client.get("/api/state").status_code, 401)
            denied = client.post(
                "/api/login", json={"token": "wrong"}, headers={"Origin": "https://relay.example.test"}
            )
            self.assertEqual(denied.status_code, 401)
            success = client.post(
                "/api/login",
                json={"token": "test-server-token-" * 3},
                headers={"Origin": "https://relay.example.test"},
            )
            self.assertEqual(success.status_code, 200)
            cookie = success.headers["set-cookie"].lower()
            self.assertIn("httponly", cookie)
            self.assertIn("secure", cookie)
            self.assertIn("samesite=strict", cookie)
            self.assertEqual(client.get("/api/state").status_code, 200)
            source = Path(self.cfg["data_dir"]) / "export.mp4"
            source.write_bytes(b"controlled-video-data")
            store = Store(self.cfg)
            cid = store.add("example", source, sha256(source))
            store.update(cid, output_path=str(source), output_hash=sha256(source), review={"approved": True})
            url = urlparse(delivery_url(self.cfg, store.get(cid)))
            client.cookies.clear()
            self.assertEqual(client.get("/api/state").status_code, 401)
            request_path = url.path + "?" + url.query
            self.assertEqual(client.head(request_path).status_code, 200)
            partial = client.get(request_path, headers={"Range": "bytes=0-3"})
            self.assertEqual(partial.status_code, 206)
            self.assertEqual(partial.content, b"cont")
            self.assertEqual(client.get(url.path + "?expires=1&signature=wrong").status_code, 403)


if __name__ == "__main__":
    unittest.main()
