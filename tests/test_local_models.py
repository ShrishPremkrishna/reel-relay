"""Shared local inference guarantees for audio and editorial providers."""

import copy
import unittest
from unittest.mock import patch

from reelrelay.local_models import chat_content, local_endpoint, require_model
from reelrelay.util import RelayError


class StoredModelIdentity(unittest.TestCase):
    def setUp(self):
        self.rows = {
            "models": [
                {"name": "editor:latest", "digest": "a" * 64, "size": 4096, "details": {"format": "gguf"}}
            ]
        }
        self.details = {
            "details": {"format": "gguf"},
            "model_info": {"general.architecture": "fixture"},
            "capabilities": ["completion", "vision"],
        }

    def request(self, url, **kwargs):
        self.assertTrue(kwargs["local_only"])
        return self.rows if url.endswith("/api/tags") else self.details

    def test_capability_and_identity_are_verified_before_inference(self):
        with patch("reelrelay.local_models.http_json", side_effect=self.request):
            identity = require_model(
                "http://localhost:11434/", "library/editor", required_capabilities=("vision",)
            )
            self.assertEqual(identity["name"], "editor:latest")
            self.assertEqual(identity["digest"], "a" * 64)
            with self.assertRaisesRegex(RelayError, "required capabilities: audio"):
                require_model("http://localhost:11434", "editor", required_capabilities=("audio",))

    def test_cloud_names_and_remote_alias_metadata_are_rejected(self):
        for name in ("editor:cloud", "editor:31b-cloud", "editor:cloud-preview"):
            with (
                self.subTest(name=name),
                patch("reelrelay.local_models.http_json") as network,
                self.assertRaises(RelayError),
            ):
                require_model("http://localhost:11434", name)
            network.assert_not_called()
        for field in ("remote_host", "remote_model"):
            original = copy.deepcopy(self.details)
            self.details[field] = "remote-reference"
            with (
                patch("reelrelay.local_models.http_json", side_effect=self.request),
                self.assertRaises(RelayError),
            ):
                require_model("http://localhost:11434", "editor")
            self.details = original

    def test_nonlocal_endpoints_and_uninstalled_models_are_rejected(self):
        for endpoint in (
            "https://ollama.com",
            "http://localhost.attacker.invalid",
            "http://token@127.0.0.1",
            "http://localhost:11434/?key=private",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(RelayError):
                local_endpoint(endpoint)
        self.rows["models"] = []
        with (
            patch("reelrelay.local_models.http_json", side_effect=self.request),
            self.assertRaisesRegex(RelayError, "not installed"),
        ):
            require_model("http://localhost:11434", "editor")


class CompletedGeneration(unittest.TestCase):
    def setUp(self):
        self.response = {
            "model": "editor",
            "done": True,
            "done_reason": "stop",
            "message": {"content": '{"verdict":"reject"}'},
        }

    def test_aliases_resolve_to_the_same_model(self):
        self.assertEqual(
            chat_content(self.response, "registry.ollama.ai/library/editor:latest"), '{"verdict":"reject"}'
        )

    def test_incomplete_wrong_model_empty_and_multibyte_oversize_are_rejected(self):
        for change in (
            {"done": False},
            {"done_reason": "length"},
            {"model": "other:latest"},
            {"message": {"content": ""}},
            {"message": None},
            {"message": {"content": "猫" * 4}},
        ):
            with self.subTest(change=change), self.assertRaises(RelayError):
                chat_content({**self.response, **change}, "editor:latest", max_bytes=10)


if __name__ == "__main__":
    unittest.main()
