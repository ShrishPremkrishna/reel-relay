import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from reelrelay.config import load, mutate
from reelrelay.controls import (
    control_service,
    installation_status,
    request_install,
    service_enabled,
    system_status,
)
from reelrelay.credentials import Vault
from reelrelay.product import asset_records
from reelrelay.server import create_app
from reelrelay.store import Store


class UISetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "config.toml"
        self.cfg = mutate(self.path, lambda cfg: None)
        self.store = Store(self.cfg)
        self.client = TestClient(create_app(self.cfg), base_url="http://127.0.0.1:8765")
        self.headers = {"Origin": "http://127.0.0.1:8765", "X-Relay-Token": self.snapshot()["csrf"]}

    def tearDown(self):
        self.client.close()
        self.temporary.cleanup()

    def snapshot(self):
        return self.client.get("/api/setup").json()

    def save(self, values):
        return self.client.post("/api/setup", json=values, headers=self.headers)

    def test_partial_settings_preserve_other_fields_and_reject_stale_save(self):
        snapshot = self.snapshot()
        self.assertEqual(
            self.save(
                {"editing": {"max_seconds": 90}, "config_version": snapshot["config_version"]}
            ).status_code,
            200,
        )
        self.assertEqual(self.save({"editing": {"min_seconds": 75}}).status_code, 200)
        self.assertEqual(load(self.path)["editing"]["max_seconds"], 90)
        rejected = self.save({"editing": {"max_seconds": 80}, "config_version": snapshot["config_version"]})
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(load(self.path)["editing"]["max_seconds"], 90)
        self.assertEqual(self.save({"editing": {"max_seconds": 20}}).status_code, 422)

    def test_controls_validate_merged_invariants_and_unknown_flags(self):
        self.assertEqual(self.save({"capture": {"retention_seconds": 60}}).status_code, 422)
        self.assertEqual(self.save({"agent_controls": {"allowed_flags": ["imaginary"]}}).status_code, 400)
        self.assertEqual(
            self.save(
                {
                    "agent_controls": {"allowed_flags": ["game_violence"], "allow_cloud_upload": True},
                    "provider": "gemini",
                }
            ).status_code,
            200,
        )
        self.assertIn("game_violence", self.snapshot()["agent_controls"]["allowed_flags"])

    def test_credentials_are_write_only_encrypted_and_removable(self):
        value = "synthetic-unit-credential-do-not-use"
        self.assertEqual(
            self.save(
                {"gemini_api_key": value, "s3_access_key_id": "fixture-id", "s3_secret_access_key": value}
            ).status_code,
            200,
        )
        snapshot = self.snapshot()
        self.assertEqual(snapshot["credential_sources"]["gemini"], "saved")
        self.assertNotIn(value, json.dumps(snapshot))
        self.assertNotIn(value, self.path.read_text())
        self.assertNotIn(value.encode(), (Path(self.cfg["data_dir"]) / "queue.sqlite3").read_bytes())
        self.assertEqual(Vault(self.cfg).get("gemini")["api_key"], value)
        self.assertEqual(self.save({"clear_gemini_key": True, "clear_s3_credentials": True}).status_code, 200)
        self.assertFalse(Vault(self.cfg).exists("gemini"))

    def test_secret_only_save_invalidates_the_old_form(self):
        self.save({})  # normalize TOML before testing a secret-only mutation
        version = self.snapshot()["config_version"]
        self.assertEqual(
            self.save({"config_version": version, "gemini_api_key": "synthetic-current-key"}).status_code, 200
        )
        self.assertNotEqual(version, self.snapshot()["config_version"])
        self.assertEqual(self.save({"config_version": version, "clear_gemini_key": True}).status_code, 409)
        self.assertTrue(Vault(self.cfg).exists("gemini"))

    def test_environment_credentials_cannot_be_misrepresented_as_cleared(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "unit-environment-value"}):
            self.assertEqual(self.snapshot()["credential_sources"]["gemini"], "environment")
            self.assertEqual(self.save({"clear_gemini_key": True}).status_code, 400)
            self.assertEqual(self.save({"gemini_api_key": "replacement"}).status_code, 400)
        self.assertEqual(self.save({"s3_access_key_id": "incomplete-pair"}).status_code, 400)

    def test_invalid_secret_input_is_not_returned_in_errors(self):
        value = "sensitive-fixture-" * 200
        response = self.save({"gemini_api_key": value})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("sensitive-fixture", response.text)
        self.assertNotIn("input", response.text)

    @patch.dict(os.environ, {"REEL_RELAY_WEB_TOKEN": "synthetic-test-token-for-remote-validation"})
    def test_public_origin_change_requires_restart(self):
        result = self.save({"delivery": {"public_base_url": "https://relay.example.com"}})
        self.assertTrue(result.json()["restart_required"])
        self.assertEqual(self.snapshot()["delivery"]["public_base_url"], "https://relay.example.com")
        self.assertTrue(self.save({"editing": {"max_seconds": 35}}).json()["restart_required"])
        self.assertFalse(self.save({"delivery": {"public_base_url": ""}}).json()["restart_required"])

    def test_public_origin_cannot_break_next_startup_without_access_token(self):
        with patch.dict(os.environ, {"REEL_RELAY_WEB_TOKEN": ""}):
            self.assertEqual(
                self.save({"delivery": {"public_base_url": "https://relay.example.com"}}).status_code, 400
            )
        self.assertEqual(load(self.path)["web"]["public_base_url"], "")

    def test_bodyless_account_post_does_not_require_a_dummy_json_body(self):
        with patch(
            "reelrelay.server.twitch_device_start",
            return_value={"verification_uri": "https://id.twitch.tv/activate"},
        ):
            response = self.client.post("/api/auth/twitch/start", headers=self.headers)
        self.assertEqual(response.status_code, 200)

    def test_invalid_delivery_addresses_cannot_be_saved(self):
        for values in (
            {"public_media_base": "http://insecure.example.com"},
            {"s3_endpoint": "https://name:password@store.example.com"},
        ):
            self.assertEqual(self.save({"delivery": values}).status_code, 422)

    def test_default_configuration_keeps_portable_paths(self):
        self.save({"editing": {"max_seconds": 45}})
        self.assertNotIn(str(self.path.parent), self.path.read_text())

    def test_pause_persists_and_start_launches_only_fixed_service(self):
        with patch("reelrelay.controls._launch", return_value=123) as launch:
            control_service(self.cfg, "capture", False)
            self.assertFalse(service_enabled(self.cfg, "capture"))
            launch.assert_not_called()
            status = control_service(self.cfg, "editor", True)
            self.assertTrue(status["managed"])
            launch.assert_called_once_with(self.cfg, ["services"], "workflow.log")
            control_service(self.cfg, "capture", True)
            self.assertEqual(launch.call_count, 1)
        self.assertEqual(self.store.snapshot()["channels"], [])

    def test_stale_supervisor_is_not_reported_as_running(self):
        self.assertFalse(system_status(self.cfg)["managed"])
        self.store.health("@service:supervisor", state="running")
        self.assertTrue(system_status(self.cfg)["managed"])
        with patch("reelrelay.controls.time.time", return_value=time.time() + 30):
            self.assertFalse(system_status(self.cfg)["managed"])

    def test_model_install_is_idempotent_while_running_and_retryable(self):
        with patch("reelrelay.store.time.time", return_value=time.time() - 1000):
            self.store.health("@service:install", state="succeeded")
        with patch("reelrelay.controls._launch", return_value=123) as launch:
            self.assertEqual(request_install(self.cfg, "tiny")["status"], "running")
            request_install(self.cfg, "tiny")
            self.assertEqual(launch.call_count, 1)
            response = self.client.post("/api/system/install", json={"model": "small"}, headers=self.headers)
            self.assertEqual(response.status_code, 400)
        with patch("reelrelay.controls.time.time", return_value=time.time() + 121):
            self.assertEqual(installation_status(self.cfg)["status"], "failed")

    def test_upload_preserves_csrf_size_and_type_boundary(self):
        for filename in ("payload.exe", "clip.mp4"):
            response = self.client.post("/api/import", params={"filename": filename}, content=b"fixture")
            self.assertEqual(response.status_code, 403)
        response = self.client.post(
            "/api/import", params={"filename": "payload.exe"}, content=b"fixture", headers=self.headers
        )
        self.assertEqual(response.status_code, 400)
        response = self.client.post(
            "/api/import",
            params={"filename": "clip.mp4"},
            content=b"fixture",
            headers={**self.headers, "Content-Length": str(101 * 1024**2)},
        )
        self.assertEqual(response.status_code, 413)

    def test_chunked_json_cannot_bypass_the_body_limit(self):
        response = self.client.post(
            "/api/setup",
            content=iter([b" " * (128 * 1024)] * 3),
            headers={**self.headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_import_has_no_filename_path_traversal_and_cleans_staging(self):
        with patch(
            "reelrelay.media.probe", return_value={"duration": 20, "streams": [{"codec_type": "video"}]}
        ):
            response = self.client.post(
                "/api/import",
                params={"filename": "../../escape.mp4", "channel": "fixture"},
                content=b"synthetic-video",
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 200, response.text)
        candidate = self.store.get(response.json()["id"])
        self.assertTrue(Path(candidate["source_path"]).is_relative_to(Path(self.cfg["data_dir"]) / "sources"))
        self.assertEqual(list((Path(self.cfg["data_dir"]) / "ingest").iterdir()), [])

    def test_asset_upload_persists_hash_metadata_and_preview_id(self):
        info = json.dumps({"streams": [{"codec_type": "audio"}], "format": {"duration": "1.0"}})
        params = {
            "filename": "effect.wav",
            "title": "Original sound",
            "kind": "sfx",
            "rights": "Self-created",
            "tags": "impact,short",
        }
        with (
            patch("reelrelay.uploads.media.run", return_value=info),
            patch("reelrelay.uploads.media.binary", return_value="ffprobe"),
        ):
            response = self.client.post(
                "/api/assets/upload", params=params, content=b"synthetic-sound", headers=self.headers
            )
            self.assertEqual(response.status_code, 200, response.text)
            repeated = self.client.post(
                "/api/assets/upload", params=params, content=b"synthetic-sound", headers=self.headers
            )
        self.assertEqual(response.json(), repeated.json())
        assets = asset_records(self.cfg)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["id"], response.json()["id"])
        self.assertEqual(assets[0]["rights"], "Self-created")
