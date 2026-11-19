from __future__ import annotations

import math
import statistics
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatMessage:
    timestamp: float
    user: str
    text: str
    message_id: str
    server_timestamp: float | None = None


class SpikeDetector:
    """Arrival-time buckets, per-user caps, warmup, robust baseline, and cooldown."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.history = deque(maxlen=cfg["baseline_windows"])
        self.bucket = None
        self.users = Counter()
        self.examples = []
        self.ids = OrderedDict()
        self.last_trigger = -math.inf
        self.ignored = {u.lower() for u in cfg["ignored_users"]}

    def _finish(self):
        count = sum(min(n, self.cfg["per_user_cap"]) for n in self.users.values())
        users = len(self.users)
        baseline = statistics.median(self.history) if self.history else 0
        mad = statistics.median(abs(n - baseline) for n in self.history) if self.history else 0
        threshold = max(self.cfg["min_messages"], baseline * self.cfg["ratio"], baseline + 4 * max(mad, 1))
        end = (self.bucket + 1) * self.cfg["window_seconds"]
        trigger = None
        if (
            len(self.history) >= self.cfg["warmup_windows"]
            and count >= threshold
            and users >= self.cfg["min_users"]
            and end - self.last_trigger >= self.cfg["cooldown_seconds"]
        ):
            self.last_trigger = end
            trigger = {
                "timestamp": end,
                "messages": count,
                "unique_users": users,
                "baseline": baseline,
                "ratio": count / max(baseline, 1),
                "threshold": threshold,
                "chat_examples": self.examples[:12],
            }
        # Winsorizing prevents a raid or one huge spike raising the baseline for minutes.
        self.history.append(min(count, max(threshold, baseline * 2)) if trigger else count)
        self.users.clear()
        self.examples = []
        return trigger

    def advance(self, timestamp):
        target = int(timestamp // self.cfg["window_seconds"])
        if self.bucket is None:
            self.bucket = target
        triggers = []
        # Bound work after a long disconnect; an old baseline should not survive it.
        if target - self.bucket > self.cfg["baseline_windows"]:
            self.history.clear()
            self.users.clear()
            self.examples = []
            self.bucket = target
        while self.bucket < target:
            trigger = self._finish()
            if trigger:
                triggers.append(trigger)
            self.bucket += 1
        return triggers

    def feed(self, message: ChatMessage):
        triggers = self.advance(message.timestamp)
        if message.message_id in self.ids or message.user.lower() in self.ignored:
            return triggers
        self.ids[message.message_id] = None
        if len(self.ids) > 20000:
            self.ids.popitem(last=False)
        if message.timestamp < self.bucket * self.cfg["window_seconds"] or message.text.startswith("!"):
            return triggers
        self.users[message.user] += 1
        if self.users[message.user] <= self.cfg["per_user_cap"] and len(self.examples) < 24:
            self.examples.append(message.text[:200])
        return triggers
