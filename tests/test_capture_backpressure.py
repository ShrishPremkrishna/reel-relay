"""Finished and held clips cannot starve deterministic live intake."""

import copy
import tempfile
import unittest

from reelrelay.capture import intake_status
from reelrelay.config import DEFAULTS
from reelrelay.store import Store


class CaptureBackpressureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.cfg = copy.deepcopy(DEFAULTS)
        self.cfg["data_dir"] = self.temp.name
        self.cfg["capture"]["max_pending"] = 2
        self.store = Store(self.cfg)
        self.serial = 0

    def tearDown(self):
        self.temp.cleanup()

    def add(self, status):
        self.serial += 1
        cid = self.store.add("fixture", f"clip-{self.serial}.mp4", f"hash-{self.serial}")
        self.store.update(cid, status=status)
        return cid

    def capacity(self, pending=0):
        return intake_status(self.store, self.cfg["capture"], pending)

    def test_paused_publishing_and_review_holds_do_not_block_new_capture(self):
        for status in (
            "approved",
            "ready",
            "uploading",
            "container_ready",
            "publishing",
            "publish_unknown",
            "published",
            "publish_failed",
            "editorial_review",
            "needs_human_review",
            "rejected",
            "failed",
        ):
            for _ in range(3):
                self.add(status)
        self.add("captured")
        self.assertEqual(
            self.capacity(),
            {
                "processing_backlog": 1,
                "processing_limit": 2,
                "intake_state": "accepting",
            },
        )

    def test_backlog_resumes_without_an_operator_when_an_export_finishes(self):
        self.add("needs_agent")
        rendered = self.add("needs_preview_review")
        self.assertEqual(self.capacity()["intake_state"], "processing_backlog_full")
        self.store.update(rendered, status="approved")
        self.assertEqual(self.capacity()["intake_state"], "accepting")

    def test_every_active_stage_consumes_capacity_including_automatic_revisions(self):
        for status in (
            "captured",
            "preparing",
            "needs_agent",
            "judging",
            "selected",
            "rendering",
            "needs_preview_review",
            "reviewing",
            "preview_changes",
            "revising",
        ):
            with self.subTest(status=status):
                first, second = self.add(status), self.add(status)
                self.assertEqual(self.capacity()["intake_state"], "processing_backlog_full")
                self.store.update(first, status="rejected")
                self.store.update(second, status="rejected")

    def test_pending_windows_remain_bounded_independently(self):
        self.assertEqual(self.capacity(2)["intake_state"], "pending_windows_full")
        self.assertEqual(self.capacity(1)["intake_state"], "accepting")
