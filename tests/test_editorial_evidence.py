"""Prompt reduction must not erase defects, timing or editorial constraints."""

import copy
import json
import unittest

from reelrelay.editorial_evidence import compact_packet


class EditorialEvidenceTests(unittest.TestCase):
    def test_editorial_and_review_context_survives_without_shared_mutable_objects(self):
        packet = {
            "stage": "review",
            "duration": 30.25,
            "source_dimensions": [1920, 1080],
            "channel": "fixture",
            "is_test": True,
            "source_sha256": "source-proof",
            "output_sha256": "export-proof",
            "chat": {"chat_examples": ["laugh"]},
            "editorial_preferences": {"pacing": "Keep the pause"},
            "previous_decision": {"keep": [{"start": 1, "end": 8}]},
            "preview_feedback": {"issues": ["The payoff is cut off"]},
            "operator_feedback": [{"notes": "Keep the reply"}],
            "revision_instruction": "Fix the caption",
            "detail_inspection": {"windows": [{"start": 5, "end": 7, "reason": "uncertain motion"}]},
            "qa": {"full_decode": "passed", "warnings": ["Clipping"], "audio_silent": False},
            "future_editorial_constraint": {"notes": ["Preserve the second speaker"]},
            "audio_review": "Measured, not listened",
            "images": ["/artifact/export/frame-2.jpg", "/artifact/export/frame-1.jpg"],
            "artifact_root": "/artifact",
            "video_path": "/artifact/export/reel.mp4",
        }
        before = copy.deepcopy(packet)
        compact = compact_packet(packet)
        for key in packet.keys() - {"images", "artifact_root", "video_path"}:
            self.assertEqual(compact[key], packet[key], key)
        self.assertEqual(compact["image_names"], ["frame-2.jpg", "frame-1.jpg"])
        compact["qa"]["warnings"].clear()
        compact["previous_decision"]["keep"][0]["start"] = 99
        compact["chat"]["chat_examples"].append("changed")
        self.assertEqual(packet, before)

    def test_words_keep_exact_values_missing_scores_and_segment_uncertainty(self):
        words = [
            {"start": 0.125, "end": 0.127, "text": "Wait", "probability": 0.0},
            {"start": 7.25, "end": 8.875, "text": "what?", "speaker": "second", "uncertain": True},
        ]
        packet = {
            "transcript": {
                "language": "en",
                "source_sha256": "source-proof",
                "words": words,
                "text": "Wait what?",
                "segments": [
                    {
                        "start": 0.125,
                        "end": 8.875,
                        "text": "Wait what?",
                        "no_speech_prob": 0.8,
                        "uncertainties": ["Possible hallucination over silence"],
                    }
                ],
            }
        }
        compact = compact_packet(packet)["transcript"]
        reconstructed = [dict(zip(compact["word_columns"], row, strict=True)) for row in compact["words"]]
        for original, recovered in zip(words, reconstructed, strict=True):
            for key, value in original.items():
                self.assertEqual(recovered[key], value)
        self.assertIsNone(reconstructed[1]["probability"])
        self.assertEqual(reconstructed[0]["probability"], 0.0)
        self.assertEqual(compact["segments"][0]["uncertainties"], ["Possible hallucination over silence"])
        self.assertEqual(compact["segments"][0]["no_speech_prob"], 0.8)
        self.assertNotIn("text", compact["segments"][0])
        self.assertNotIn("text", compact)
        self.assertEqual(packet["transcript"]["words"], words)

    def test_nonduplicated_segment_content_and_segment_only_transcripts_survive(self):
        for words in ([], [{"start": 1, "end": 2, "text": "partial"}]):
            with self.subTest(words=words):
                transcript = {
                    "words": words,
                    "segments": [{"start": 1, "end": 3, "text": "partial missing payoff"}],
                    "notes": "Word alignment is incomplete",
                }
                compact = compact_packet({"transcript": transcript})["transcript"]
                self.assertEqual(compact["segments"], transcript["segments"])
                self.assertEqual(compact["notes"], transcript["notes"])

    def test_perception_retains_every_window_defect_and_unverified_speech(self):
        assessment = {
            "summary": "A reply follows a pause",
            "audio_issues": ["Speech masked by music"],
            "visual_issues": ["Face cut off"],
            "speech": [{"start": 0, "end": 20, "text": "Maybe yes"}],
            "confidence": 0.2,
            "events": [],
        }
        window = {
            "start": 0,
            "end": 20,
            "model": "listener",
            "timing_quality": "window-level only",
            "method": "audio model",
            "audio_path": "/private/work/input.wav",
            "audio_sha256": "digest",
            "model_digest": "weights",
            "wall_seconds": 8,
            "eval_count": 200,
            "signal": {"digital_silence": False, "no_sustained_signal": False, "peak_pcm": 32768},
            "observation": {
                "summary": "Maybe a word",
                "heard_speech": "uncertain quotation",
                "delivery": "overlapping speakers",
                "confidence": 0.1,
                "uncertainties": ["Last word unclear", "Could be background audio"],
                "audio_issues": ["A separate defect absent from the aggregate"],
            },
        }
        perception = {
            "source_sha256": "source-proof",
            "provider": "local",
            "model": "listener",
            "modalities": ["audio"],
            "duration": 20,
            "timing_quality": "window-level only",
            "speech_verification": "Unverified; compare with ASR",
            "assessment": assessment,
            "inputs": [window, {"start": 20, "end": 30, "observation": {"uncertainties": ["No audio"]}}],
            "raw_observations": [{"start": 3, "end": 4, "uncertainties": ["Other observer uncertain"]}],
        }
        compact = compact_packet({"perception": perception})["perception"]
        self.assertEqual(compact["assessment"], assessment)
        for field in (
            "source_sha256",
            "provider",
            "model",
            "modalities",
            "timing_quality",
            "speech_verification",
        ):
            self.assertEqual(compact[field], perception[field])
        self.assertEqual(compact["inputs"][0]["observation"], window["observation"])
        self.assertEqual(compact["inputs"][0]["signal"], window["signal"])
        self.assertEqual(compact["inputs"][1], perception["inputs"][1])
        self.assertEqual(compact["raw_observations"], perception["raw_observations"])
        for field in ("audio_path", "audio_sha256", "model_digest", "wall_seconds", "eval_count"):
            self.assertNotIn(field, compact["inputs"][0])

    def test_exact_asset_references_and_complete_rights_survive_without_provenance(self):
        asset = {
            "path": "library/odd path/[reaction]'s.mp4",
            "kind": "overlay",
            "title": "Reaction",
            "tags": ["awkward pause"],
            "duration": 1.23456789,
            "rights": "Reference details. " * 30 + "Not permitted for commercial reuse.",
            "source_url": "https://example.test/source",
            "sha256": "asset-proof",
            "attribution": "Full credit belongs in original export receipts",
            "usage_notes": "Original note",
        }
        packet = {"assets": [asset]}
        compact = compact_packet(packet)["assets"][0]
        self.assertEqual(set(compact), {"path", "kind", "title", "tags", "duration", "rights"})
        for key in compact:
            self.assertEqual(compact[key], asset[key])
        compact["tags"].append("changed")
        self.assertEqual(asset["tags"], ["awkward pause"])
        self.assertIn("attribution", asset)

    def test_credentials_and_raw_responses_never_enter_prompt_and_compaction_is_idempotent(self):
        packet = {
            "apiKey": "DO_NOT_SEND",
            "OPENAI_API_KEY": "DO_NOT_SEND",
            "Authorization": "DO_NOT_SEND",
            "raw_response": {"choices": ["DO_NOT_SEND"]},
            "perception": {
                "assessment": {"audio_issues": ["A real defect"]},
                "inputs": [
                    {
                        "start": 0,
                        "end": 2,
                        "access_token": "DO_NOT_SEND",
                        "response": {"raw": "DO_NOT_SEND"},
                        "observation": {"uncertainties": ["Speech unclear"], "client_secret": "DO_NOT_SEND"},
                    }
                ],
            },
            "transcript": {
                "words": [{"start": 0, "end": 1, "text": "Hi", "probability": 0.5}],
                "segments": [{"start": 0, "end": 1, "text": "Hi"}],
            },
        }
        compact = compact_packet(packet)
        self.assertNotIn("DO_NOT_SEND", json.dumps(compact))
        self.assertEqual(compact["perception"]["assessment"]["audio_issues"], ["A real defect"])
        self.assertEqual(
            compact["perception"]["inputs"][0]["observation"]["uncertainties"], ["Speech unclear"]
        )
        self.assertNotIn("segments", compact["transcript"])
        self.assertEqual(compact_packet(compact), compact)
