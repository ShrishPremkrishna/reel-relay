"""Local audio is explicit, bounded, source-bound, and never a visual review."""

import base64
import copy
import io
import json
import os
import tempfile
import unittest
import urllib.request
import wave
from pathlib import Path
from unittest.mock import patch

from reelrelay import perception
from reelrelay.config import DEFAULTS
from reelrelay.util import RelayError, http_json, read_json, sha256


def listening_note(**changes):
    result = {
        "summary": "A person reacts to a long food order.",
        "heard_speech": "Did you get all that?",
        "delivery": "A pause followed by an incredulous response.",
        "audio_issues": [],
        "uncertainties": ["The setting cannot be seen."],
        "confidence": 0.8,
    }
    result.update(changes)
    return result


def completed(note=None, **changes):
    result = {
        "model": "local-audio:latest",
        "done": True,
        "done_reason": "stop",
        "message": {"content": json.dumps(note if note is not None else listening_note())},
        "eval_count": 100,
        "eval_duration": 100000000,
    }
    result.update(changes)
    return result


class LocalTransport(unittest.TestCase):
    def test_loopback_ignores_environment_proxy_and_canonicalizes_localhost(self):
        opener = unittest.mock.MagicMock()
        opener.open.return_value.__enter__.return_value = io.BytesIO(b'{"ok":true}')
        with (
            patch.dict(os.environ, {"http_proxy": "http://external.invalid:9999", "no_proxy": ""}),
            patch("urllib.request.build_opener", return_value=opener) as build,
        ):
            self.assertEqual(http_json("http://localhost:11434/api/tags", local_only=True), {"ok": True})
        self.assertEqual(opener.open.call_args.args[0].full_url, "http://127.0.0.1:11434/api/tags")
        proxy = next(
            handler for handler in build.call_args.args if isinstance(handler, urllib.request.ProxyHandler)
        )
        self.assertEqual(proxy.proxies, {})

    def test_external_credentialed_and_disguised_hosts_are_refused_before_network(self):
        for url in (
            "https://127.0.0.1:11434",
            "http://localhost.attacker.invalid/api",
            "http://127.0.0.1@attacker.invalid",
            "http://user@localhost:11434",
            "http://127.0.0.2/",
        ):
            with (
                self.subTest(url=url),
                patch("urllib.request.build_opener") as build,
                self.assertRaises(RelayError),
            ):
                http_json(url, local_only=True)
            build.assert_not_called()


class AudioContract(unittest.TestCase):
    def test_partial_empty_oversized_and_bad_json_cannot_become_evidence(self):
        responses = [
            completed(done=False),
            completed(done_reason="length"),
            completed(message={"content": ""}),
            completed(message={"content": "{"}),
            completed(message={"content": " " * 17000}),
            completed(listening_note(confidence=float("nan"))),
            completed(listening_note(confidence=5)),
            completed(listening_note(confidence=True)),
            completed(listening_note(summary=None)),
            completed(listening_note(start=0, end=0)),
            completed(listening_note(summary="concrete sounds or audible exchange.")),
            completed(
                listening_note(
                    heard_speech="Two number nine. Listen carefully to the attached recording and report what you actually hear."
                )
            ),
        ]
        for response in responses:
            with self.subTest(response=str(response)[:100]), self.assertRaises(RelayError):
                perception._decode_audio_response(response)

    def test_valid_listening_notes_have_no_time_or_visual_fields(self):
        self.assertEqual(perception._decode_audio_response(completed()), listening_note())
        schema = perception.AudioWindowAssessment.model_json_schema()["properties"]
        self.assertEqual(
            set(schema),
            {"summary", "heard_speech", "delivery", "audio_issues", "uncertainties", "confidence"},
        )


class AudioReceiptIntegration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"source identity fixture")
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = str(self.root)
        self.cfg["perception"].update(provider="ollama", model="local-audio:latest")
        self.duration = 45.0
        self.requests = []
        self.silent = False
        self.inventory = {
            "models": [
                {
                    "name": "local-audio:latest",
                    "digest": "a" * 64,
                    "size": 1024,
                    "details": {"format": "gguf"},
                }
            ]
        }
        self.metadata = {
            "capabilities": ["audio", "completion"],
            "details": {"format": "gguf"},
            "model_info": {"general.architecture": "audio-fixture"},
        }

    def tearDown(self):
        self.temporary.cleanup()

    def fake_http(self, url, **kwargs):
        self.assertTrue(kwargs["local_only"])
        if url.endswith("/api/tags"):
            return self.inventory
        if url.endswith("/api/show"):
            return self.metadata
        self.requests.append(kwargs["data"])
        return completed()

    def decode_audio(self, args, root, **kwargs):
        target = Path(args[-1])
        duration = float(args[args.index("-t") + 1])
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as stream:
            stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            stream.writeframes((b"\0\0" if self.silent else b"\x00\x08") * round(16000 * duration))

    def inspect(self, http=None, decode=None):
        with (
            patch("reelrelay.perception.http_json", side_effect=http or self.fake_http),
            patch("reelrelay.local_models.http_json", side_effect=http or self.fake_http),
            patch(
                "reelrelay.perception.media.probe",
                return_value={"duration": self.duration, "streams": [{"codec_type": "audio"}]},
            ),
            patch("reelrelay.perception.media.run", side_effect=decode or self.decode_audio),
        ):
            return perception.inspect_media(self.cfg, self.source, purpose="selection")

    def test_receipts_cover_known_windows_and_send_only_actual_audio(self):
        path = self.inspect()
        report = read_json(path)
        self.assertEqual(report["modalities"], ["audio"])
        self.assertEqual(report["timing_quality"], "window-level; ASR authoritative")
        self.assertEqual(report["source_sha256"], sha256(self.source))
        self.assertEqual(
            [(x["start"], x["end"]) for x in report["assessment"]["events"]], [(0, 15), (15, 30), (30, 45)]
        )
        self.assertTrue(all(x["visual"] == "" for x in report["assessment"]["events"]))
        self.assertEqual(report["assessment"]["visual_issues"], [])
        self.assertEqual(len(self.requests), 3)
        for payload in self.requests:
            message = payload["messages"][0]
            self.assertEqual(len(message["images"]), 1)
            self.assertEqual(base64.b64decode(message["images"][0])[:4], b"RIFF")
            self.assertNotIn("Did you get all that", message["content"])
            self.assertFalse(payload["think"])
        for receipt in report["inputs"]:
            self.assertEqual(receipt["audio_sha256"], sha256(Path(receipt["audio_path"])))
            self.assertEqual(receipt["model_digest"], "a" * 64)
            self.assertIn("setting cannot be seen", receipt["observation"]["uncertainties"][0])
        self.assertEqual(perception.read_verified(path, sha256(self.source), self.root), report)
        with self.assertRaises(RelayError):
            perception.read_verified(path, "wrong", self.root)

    def test_balanced_windows_avoid_a_short_tail_without_extra_model_calls(self):
        self.duration = 24.7
        report = read_json(self.inspect())
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(
            [(row["start"], row["end"]) for row in report["inputs"]], [(0, 12.35), (12.35, 24.7)]
        )
        self.assertTrue(all(row["signal"]["frames"] == 197600 for row in report["inputs"]))

    def test_silence_is_measured_without_invented_model_speech(self):
        self.silent = True
        report = read_json(self.inspect())
        self.assertEqual(self.requests, [])
        self.assertEqual(report["assessment"]["speech"], [])
        self.assertEqual(len(report["assessment"]["audio_issues"]), 3)
        self.assertTrue(all(x["signal"]["digital_silence"] for x in report["inputs"]))
        self.assertTrue(all(x["response_sha256"] is None for x in report["inputs"]))

    def test_isolated_capture_click_does_not_generate_a_fake_conversation(self):
        self.silent = True

        def click(args, root, **kwargs):
            self.decode_audio(args, root, **kwargs)
            target = Path(args[-1])
            with wave.open(str(target), "rb") as stream:
                params = stream.getparams()
                samples = bytearray(stream.readframes(stream.getnframes()))
            samples[32000:32002] = b"\xff\x7f"
            with wave.open(str(target), "wb") as stream:
                stream.setparams(params)
                stream.writeframes(samples)

        report = read_json(self.inspect(decode=click))
        self.assertFalse(self.requests)
        self.assertEqual(report["assessment"]["speech"], [])
        self.assertTrue(all(row["signal"]["no_sustained_signal"] for row in report["inputs"]))
        self.assertTrue(all("measurement" in row["method"] for row in report["inputs"]))

    def test_remote_or_uninstalled_models_are_refused_before_audio_extraction(self):
        for modification in (
            "cloud-tag",
            "remote-host",
            "remote-model",
            "no-audio",
            "missing-inventory",
            "bad-digest",
            "missing-metadata",
        ):
            with self.subTest(modification=modification):
                old_cfg, old_inventory, old_metadata = (
                    copy.deepcopy(self.cfg),
                    copy.deepcopy(self.inventory),
                    copy.deepcopy(self.metadata),
                )
                if modification == "cloud-tag":
                    self.cfg["perception"]["model"] = "gemma4:31b-cloud"
                elif modification == "remote-host":
                    self.metadata["remote_host"] = "https://remote.invalid"
                elif modification == "remote-model":
                    self.metadata["remote_model"] = "remote"
                elif modification == "no-audio":
                    self.metadata["capabilities"] = ["vision"]
                elif modification == "missing-inventory":
                    self.inventory["models"] = []
                elif modification == "bad-digest":
                    self.inventory["models"][0]["digest"] = "not-a-digest"
                else:
                    self.metadata.pop("model_info")
                with patch("reelrelay.perception.media.run") as decode, self.assertRaises(RelayError):
                    self.inspect(decode=decode)
                decode.assert_not_called()
                self.cfg, self.inventory, self.metadata = old_cfg, old_inventory, old_metadata

    def test_incomplete_or_invalid_responses_leave_raw_evidence_but_no_report(self):
        def incomplete(url, **kwargs):
            return (
                completed(done_reason="length")
                if url.endswith("/api/chat")
                else self.fake_http(url, **kwargs)
            )

        with self.assertRaises(RelayError):
            self.inspect(http=incomplete)
        self.assertTrue(list((self.root / "perception").glob("*/part-00/response.json")))
        failures = list((self.root / "perception").glob("*/failure.json"))
        self.assertEqual(len(failures), 1)
        self.assertFalse(read_json(failures[0])["complete"])
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

    def test_confidence_scale_error_gets_one_same_audio_retry_with_both_receipts(self):
        self.duration = 5.0
        requests = []

        def wrong_scale_then_valid(url, **kwargs):
            if not url.endswith("/api/chat"):
                return self.fake_http(url, **kwargs)
            requests.append(kwargs["data"])
            return completed(
                listening_note(summary="Untrusted rejected model prose", confidence=5)
                if len(requests) == 1
                else listening_note()
            )

        report = read_json(self.inspect(http=wrong_scale_then_valid))
        self.assertEqual(len(requests), 2)
        messages = [request["messages"][0] for request in requests]
        self.assertEqual(messages[0]["images"], messages[1]["images"])
        self.assertIn("confidence: less_than_equal", messages[1]["content"])
        self.assertNotIn("Untrusted rejected model prose", messages[1]["content"])
        receipt = report["inputs"][0]
        self.assertEqual(receipt["observation"], listening_note())
        self.assertEqual([row["valid"] for row in receipt["attempts"]], [False, True])
        self.assertEqual(receipt["response_path"], receipt["attempts"][1]["response_path"])
        self.assertEqual(receipt["response_sha256"], receipt["attempts"][1]["response_sha256"])
        for attempt in receipt["attempts"]:
            self.assertEqual(sha256(attempt["response_path"]), attempt["response_sha256"])
        original = read_json(receipt["attempts"][0]["response_path"])
        self.assertEqual(json.loads(original["message"]["content"])["confidence"], 5)

    def test_repeated_schema_error_fails_after_exactly_one_retry(self):
        self.duration = 5.0
        requests = []

        def wrong_scale(url, **kwargs):
            if not url.endswith("/api/chat"):
                return self.fake_http(url, **kwargs)
            requests.append(kwargs["data"])
            return completed(listening_note(confidence=5))

        with self.assertRaisesRegex(RelayError, "confidence: less_than_equal"):
            self.inspect(http=wrong_scale)
        self.assertEqual(len(requests), 2)
        attempts = list((self.root / "perception").glob("*/part-00/response-attempts.json"))
        self.assertEqual(len(attempts), 1)
        self.assertEqual([row["valid"] for row in read_json(attempts[0])], [False, False])
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))
        self.assertTrue(list((self.root / "perception").glob("*/failure.json")))

    def test_incomplete_wrong_identity_oversized_and_echoed_responses_never_retry(self):
        self.duration = 5.0
        for response in (
            completed(done=False),
            completed(done_reason="length"),
            completed(model="unrequested:latest"),
            completed(message={"content": "x" * 17000}),
            completed(listening_note(summary="concrete sounds or audible exchange.")),
        ):
            requests = []

            def invalid(url, response=response, requests=requests, **kwargs):
                if not url.endswith("/api/chat"):
                    return self.fake_http(url, **kwargs)
                requests.append(kwargs["data"])
                return response

            with self.subTest(response=str(response)[:100]), self.assertRaises(RelayError):
                self.inspect(http=invalid)
            self.assertEqual(len(requests), 1)
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

    def test_model_changes_or_wrong_response_identity_are_rejected(self):
        calls = 0

        def changed_model(url, **kwargs):
            nonlocal calls
            result = self.fake_http(url, **kwargs)
            if url.endswith("/api/tags"):
                calls += 1
                if calls > 1:
                    result = copy.deepcopy(result)
                    result["models"][0]["digest"] = "b" * 64
            return result

        with self.assertRaisesRegex(RelayError, "model changed"):
            self.inspect(http=changed_model)
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))

        def wrong_response(url, **kwargs):
            return (
                completed(model="unrequested:latest")
                if url.endswith("/api/chat")
                else self.fake_http(url, **kwargs)
            )

        with self.assertRaisesRegex(RelayError, "different model"):
            self.inspect(http=wrong_response)

    def test_old_or_invalid_receipts_cannot_be_reused(self):
        path = self.inspect()
        report = read_json(path)
        report["adapter_version"] = 1
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(RelayError, "current window-level adapter"):
            perception.read_verified(path, sha256(self.source), self.root)
        report["adapter_version"] = 2
        report["assessment"]["confidence"] = 5
        path.write_text(json.dumps(report))
        with self.assertRaises(RelayError):
            perception.read_verified(path, sha256(self.source), self.root)

    def test_previously_saved_instruction_echo_is_not_reused(self):
        path = self.inspect()
        report = read_json(path)
        report["inputs"][0]["observation"]["heard_speech"] = (
            "An audible sentence. Listen carefully to the attached recording and report what you actually hear."
        )
        path.write_text(json.dumps(report))
        with self.assertRaisesRegex(RelayError, "echoed instructions"):
            perception.read_verified(path, sha256(self.source), self.root)

    def test_source_or_model_changes_cannot_produce_a_bound_report(self):
        def changed_source(url, **kwargs):
            result = self.fake_http(url, **kwargs)
            if url.endswith("/api/chat"):
                self.source.write_bytes(b"source changed during review")
            return result

        with self.assertRaisesRegex(RelayError, "Clip changed"):
            self.inspect(http=changed_source)
        self.assertFalse(list((self.root / "perception").glob("*/report.json")))


if __name__ == "__main__":
    unittest.main()
