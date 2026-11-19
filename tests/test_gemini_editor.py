"""The optional cloud editor requires consent, preserves receipts and rejects partial JSON."""

import base64
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reelrelay.agent import judge
from reelrelay.config import DEFAULTS
from reelrelay.util import RelayError, read_json, sha256, write_json
from tests.test_pipeline import decision


def complete(value=None, **changes):
    result = {
        "modelVersion": "operator-chosen-model",
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps(value if value is not None else decision())}]},
            }
        ],
    }
    result.update(changes)
    return result


class GeminiEditor(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.cfg["agent"].update(provider="gemini", model="operator-chosen-model", allow_cloud_upload=True)
        self.image = self.root / "source.png"
        self.image.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6g2AAAAAASUVORK5CYII="
            )
        )
        self.evidence = self.root / "evidence.json"
        write_json(
            self.evidence,
            {
                "stage": "select",
                "artifact_root": str(self.root),
                "images": [str(self.image)],
                "duration": 30,
                "assets": [],
                "credentials": {"api_key": "must-never-upload"},
            },
        )

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, response=None, side_effect=None):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic-test-key"}),
            patch(
                "reelrelay.agent.http_json", return_value=response or complete(), side_effect=side_effect
            ) as http,
        ):
            value = judge(self.cfg, self.evidence)
        return value, http

    def test_official_structured_image_request_includes_preferences_without_secrets(self):
        self.cfg["editing"].update(
            layout="focus", style_notes="Keep the reaction hold", overlays_enabled=False, sfx_gain_db=-26
        )
        value, http = self.call()
        self.assertEqual(value["layout"], "focus")
        self.assertEqual(
            http.call_args.args[0],
            "https://generativelanguage.googleapis.com/v1beta/models/operator-chosen-model:generateContent",
        )
        request = http.call_args.kwargs
        self.assertEqual(request["headers"], {"x-goog-api-key": "synthetic-test-key"})
        self.assertEqual(request["data"]["generationConfig"]["responseMimeType"], "application/json")
        self.assertEqual(request["data"]["generationConfig"]["responseJsonSchema"]["type"], "object")
        schema = request["data"]["generationConfig"]["responseJsonSchema"]
        self.assertEqual(schema["properties"]["overlays"]["maxItems"], 0)
        self.assertEqual(schema["properties"]["sfx"]["items"]["properties"]["gain_db"]["maximum"], -26)
        self.assertNotIn("maximum", schema["properties"]["score"])
        parts = request["data"]["contents"][0]["parts"]
        self.assertEqual(base64.b64decode(parts[1]["inlineData"]["data"]), self.image.read_bytes())
        self.assertIn("Keep the reaction hold", parts[0]["text"])
        self.assertIn("major_spoilers", parts[0]["text"])
        self.assertNotIn("must-never-upload", parts[0]["text"])
        self.assertNotIn("synthetic-test-key", json.dumps(request["data"]))
        self.assertNotIn("tools", request["data"])
        raw = next(self.root.glob("*-gemini-response.json"))
        self.assertNotIn("synthetic-test-key", raw.read_text())
        receipt = read_json(raw)
        self.assertEqual(receipt["evidence_sha256"], sha256(self.evidence))
        self.assertEqual(receipt["images"][0]["sha256"], sha256(self.image))

    def test_cloud_consent_and_explicit_model_are_required_before_network(self):
        for changes in ({"allow_cloud_upload": False}, {"model": ""}, {"model": "../../models/other"}):
            previous = copy.deepcopy(self.cfg["agent"])
            self.cfg["agent"].update(changes)
            with (
                self.subTest(changes=changes),
                patch("reelrelay.agent.http_json") as http,
                patch("reelrelay.agent.Vault") as vault,
                self.assertRaises(RelayError),
            ):
                judge(self.cfg, self.evidence)
            http.assert_not_called()
            vault.assert_not_called()
            self.cfg["agent"] = previous

    def test_encrypted_key_is_supported_without_persisting_it(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": ""}),
            patch("reelrelay.agent.Vault") as vault,
            patch("reelrelay.agent.http_json", return_value=complete()) as http,
        ):
            vault.return_value.exists.return_value = True
            vault.return_value.get.return_value = {"api_key": "encrypted-fixture-key"}
            judge(self.cfg, self.evidence)
        self.assertEqual(http.call_args.kwargs["headers"]["x-goog-api-key"], "encrypted-fixture-key")
        self.assertFalse(
            any("encrypted-fixture-key" in path.read_text() for path in self.root.glob("*.json"))
        )

    def test_missing_key_and_oversized_image_fail_before_any_network_request(self):
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": ""}),
            patch("reelrelay.agent.Vault") as vault,
            patch("reelrelay.agent.http_json") as http,
            self.assertRaisesRegex(RelayError, "Connect a Gemini key"),
        ):
            vault.return_value.exists.return_value = False
            judge(self.cfg, self.evidence)
        http.assert_not_called()
        with self.image.open("wb") as image:
            image.truncate(8 * 1024**2 + 1)
        with (
            patch.dict(os.environ, {"GEMINI_API_KEY": "synthetic-test-key"}),
            patch("reelrelay.agent.http_json") as http,
            self.assertRaisesRegex(RelayError, "bounded local image"),
        ):
            judge(self.cfg, self.evidence)
        http.assert_not_called()

    def test_partial_blocked_malformed_and_tool_responses_cannot_become_decisions(self):
        bad = []
        for reason in ("MAX_TOKENS", "SAFETY", "RECITATION"):
            result = complete()
            result["candidates"][0]["finishReason"] = reason
            bad.append(result)
        bad.extend(
            [
                complete(candidates=[]),
                complete(candidates=[{}, {}]),
                complete(value={"verdict": "accept"}),
                complete(promptFeedback={"blockReason": "SAFETY"}),
            ]
        )
        for content in ("{", " ", "x" * 65537):
            result = complete()
            result["candidates"][0]["content"]["parts"][0]["text"] = content
            bad.append(result)
        result = complete()
        result["candidates"][0]["content"]["parts"].append({"functionCall": {"name": "send_message"}})
        bad.append(result)
        for result in bad:
            with self.subTest(result=str(result)[:120]), self.assertRaises(RelayError):
                self.call(result)
        self.assertEqual(len(list(self.root.glob("*-gemini-response.json"))), len(bad))

    def test_private_nonimage_and_changed_evidence_are_rejected(self):
        private = self.root / ".private/account.png"
        private.parent.mkdir()
        private.write_bytes(self.image.read_bytes())
        packet = read_json(self.evidence)
        packet["images"] = [str(private)]
        write_json(self.evidence, packet)
        with patch("reelrelay.agent.http_json") as http, self.assertRaises(RelayError):
            self.call()
        http.assert_not_called()
        packet["images"] = [str(self.image)]
        write_json(self.evidence, packet)
        self.image.write_bytes(b"not an image")
        with self.assertRaisesRegex(RelayError, "supports JPEG"):
            self.call()
        self.image.write_bytes(b"\xff\xd8\xfffixture")

        def changed(*args, **kwargs):
            self.evidence.write_text("{}")
            return complete()

        with self.assertRaisesRegex(RelayError, "changed during"):
            self.call(side_effect=changed)

    def test_review_uses_frozen_export_preferences(self):
        packet = read_json(self.evidence)
        packet.update(stage="review", editing={**DEFAULTS["editing"], "captions_enabled": False})
        write_json(self.evidence, packet)
        review = {
            "approved": True,
            "confidence": 0.8,
            "issues": [],
            "notes": "Frames only",
            "audio_review": "not_listened",
        }
        _, http = self.call(complete(review))
        prompt = http.call_args.kwargs["data"]["contents"][0]["parts"][0]["text"]
        self.assertIn('"captions_enabled": false', prompt)


if __name__ == "__main__":
    unittest.main()
