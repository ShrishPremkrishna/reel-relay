"""Durable workflow state with atomic claims and fenced, renewable leases."""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from . import db
from .util import RelayError

LEASE_RECOVERY = {
    "preparing": "captured",
    "judging": "needs_agent",
    "rendering": "selected",
    "reviewing": "needs_preview_review",
    "revising": "preview_changes",
}


class LeaseLost(RelayError):
    pass


class LockBusy(RelayError):
    """Another live service owns a lock; this is not a candidate failure."""


class LockHandle:
    def __init__(self, name, owner, lost):
        self.name, self.owner, self.lost = name, owner, lost


class Store:
    PATH_FIELDS = frozenset({"source_path", "project_path", "evidence_path", "output_path"})

    def __init__(self, root, database_url=None):
        if isinstance(root, dict):
            database_url = root.get("database_url") or database_url
            root = root["data_dir"]
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "queue.sqlite3"
        self.engine = db.engine_for(db.url_for(self.root, database_url))
        db.migrate(self.engine)

    @contextmanager
    def connect(self, *, write=False):
        with self.engine.connect() as connection:
            if write and self.engine.dialect.name == "sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.begin()
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise

    def insert(self, table):
        return (pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert)(table)

    def row_lock(self, query, *, skip=False):
        return query.with_for_update(skip_locked=skip) if self.engine.dialect.name == "postgresql" else query

    def assert_guard(self, connection, guard):
        if guard is None:
            return
        row = (
            connection.execute(self.row_lock(sa.select(db.locks).where(db.locks.c.name == guard.name)))
            .mappings()
            .first()
        )
        if guard.lost.is_set() or not row or row["owner"] != guard.owner or row["until"] <= time.time():
            raise LeaseLost("Service ownership expired; discarding the stale operation")

    def file_key(self, value):
        path = Path(value)
        if path.is_absolute():
            try:
                return str(path.resolve().relative_to(self.root))
            except ValueError:
                raise RelayError("Queued media must be inside the configured data directory") from None
        if ".." in path.parts:
            raise RelayError("Media paths cannot escape the data directory")
        return str(path)

    def decode(self, row):
        if row is None:
            return None
        result = dict(row)
        for key in ("metadata", "decision", "review"):
            if result.get(key):
                result[key] = json.loads(result[key])
        for key in self.PATH_FIELDS:
            if result.get(key) and not Path(result[key]).is_absolute():
                result[key] = str(self.root / result[key])
        return result

    def event(self, kind, detail, candidate_id=None):
        with self.connect(write=True) as c:
            c.execute(
                db.events.insert().values(
                    at=time.time(),
                    kind=kind,
                    candidate_id=candidate_id,
                    detail=json.dumps(detail, allow_nan=False),
                )
            )

    def add(
        self,
        channel,
        source_path,
        source_hash,
        *,
        session_id=None,
        start=0,
        end=0,
        metadata=None,
        is_test=False,
        attribution="",
        guard=None,
    ):
        now, cid = time.time(), uuid.uuid4().hex[:16]
        with self.connect(write=True) as c:
            self.assert_guard(c, guard)
            if session_id and self.engine.dialect.name == "postgresql":
                c.execute(
                    sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "capture:" + session_id},
                )
            old = c.execute(
                sa.select(db.candidates.c.id).where(db.candidates.c.source_hash == source_hash)
            ).scalar()
            if old:
                return old
            if session_id:
                overlaps = c.execute(
                    sa.select(db.candidates.c.id, db.candidates.c.start, db.candidates.c.end).where(
                        db.candidates.c.session_id == session_id,
                        db.candidates.c.end > start,
                        db.candidates.c.start < end,
                    )
                ).mappings()
                for old in overlaps:
                    overlap = min(end, old["end"]) - max(start, old["start"])
                    if overlap > 0.6 * min(end - start, old["end"] - old["start"]):
                        return old["id"]
            result = c.execute(
                self.insert(db.candidates)
                .values(
                    id=cid,
                    channel=channel,
                    status="captured",
                    created_at=now,
                    updated_at=now,
                    session_id=session_id,
                    start=start,
                    end=end,
                    source_path=self.file_key(source_path),
                    source_hash=source_hash,
                    metadata=json.dumps(metadata or {}),
                    is_test=int(is_test),
                    attribution=attribution,
                )
                .on_conflict_do_nothing(index_elements=["source_hash"])
            )
            if result.rowcount != 1:
                return c.execute(
                    sa.select(db.candidates.c.id).where(db.candidates.c.source_hash == source_hash)
                ).scalar_one()
            c.execute(
                db.events.insert().values(
                    at=now,
                    kind="captured",
                    candidate_id=cid,
                    detail=json.dumps({"channel": channel, "duration": end - start}),
                )
            )
        return cid

    def get(self, cid):
        with self.connect() as c:
            row = c.execute(sa.select(db.candidates).where(db.candidates.c.id == cid)).mappings().first()
        if row is None:
            raise RelayError("Unknown candidate")
        return self.decode(row)

    def list(self, status=None, limit=100):
        query = sa.select(db.candidates).order_by(db.candidates.c.created_at.desc()).limit(limit)
        if status:
            query = query.where(
                db.candidates.c.status.in_(status)
                if isinstance(status, (tuple, list, set))
                else db.candidates.c.status == status
            )
        with self.connect() as c:
            return [self.decode(row) for row in c.execute(query).mappings()]

    def next_publishable(self):
        """Find the oldest due real export across the entire queue."""
        query = (
            sa.select(db.candidates)
            .where(
                db.candidates.c.status.in_(("approved", "uploading", "container_ready")),
                db.candidates.c.is_test == 0,
                db.candidates.c.available_at <= time.time(),
            )
            .order_by(db.candidates.c.created_at, db.candidates.c.id)
            .limit(1)
        )
        with self.connect() as c:
            return self.decode(c.execute(query).mappings().first())

    def update(self, cid, *, expected=None, expected_version=None, claim_token=None, guard=None, **values):
        allowed = {
            "status",
            "project_path",
            "evidence_path",
            "decision",
            "score",
            "confidence",
            "caption",
            "output_path",
            "output_hash",
            "review",
            "public_url",
            "container_id",
            "media_id",
            "permalink",
            "published_at",
            "lease_until",
            "error",
            "metadata",
            "available_at",
            "publishing_account_id",
        }
        if set(values) - allowed:
            raise ValueError("Invalid candidate field")
        values["updated_at"] = time.time()
        for key in self.PATH_FIELDS & values.keys():
            if values[key]:
                values[key] = self.file_key(values[key])
        for key in ("decision", "review", "metadata"):
            if key in values:
                values[key] = json.dumps(values[key], allow_nan=False)
        if "status" in values and values["status"] not in LEASE_RECOVERY:
            values.update(claim_token=None, lease_until=None)
        query = db.candidates.update().where(db.candidates.c.id == cid)
        if expected:
            query = query.where(db.candidates.c.status == expected)
        if expected_version is not None:
            query = query.where(db.candidates.c.updated_at == expected_version)
        if claim_token:
            query = query.where(
                db.candidates.c.claim_token == claim_token, db.candidates.c.lease_until > time.time()
            )
        if guard:
            query = query.where(
                sa.exists(
                    sa.select(db.locks.c.name).where(
                        db.locks.c.name == guard.name,
                        db.locks.c.owner == guard.owner,
                        db.locks.c.until > time.time(),
                    )
                )
            )
        with self.connect(write=True) as c:
            self.assert_guard(c, guard)
            if c.execute(query.values(**values)).rowcount != 1:
                raise LeaseLost("Candidate changed or its worker lease expired; reload it")

    def claim(self, status, working, *, cid=None, lease_seconds=180):
        now, token = time.time(), uuid.uuid4().hex
        query = (
            sa.select(db.candidates)
            .where(db.candidates.c.status == status, db.candidates.c.available_at <= now)
            .order_by(db.candidates.c.created_at)
            .limit(1)
        )
        if cid:
            query = query.where(db.candidates.c.id == cid)
        with self.connect(write=True) as c:
            row = c.execute(self.row_lock(query, skip=True)).mappings().first()
            if not row:
                return None
            c.execute(
                db.candidates.update()
                .where(db.candidates.c.id == row["id"])
                .values(
                    status=working,
                    claim_token=token,
                    lease_until=now + lease_seconds,
                    updated_at=now,
                    attempts=db.candidates.c.attempts + 1,
                )
            )
            result = self.decode(row)
            result.update(
                status=working,
                claim_token=token,
                lease_until=now + lease_seconds,
            )
            return result

    def recover(self):
        with self.connect(write=True) as c:
            for working, ready in LEASE_RECOVERY.items():
                c.execute(
                    db.candidates.update()
                    .where(db.candidates.c.status == working, db.candidates.c.lease_until < time.time())
                    .values(
                        status=ready,
                        claim_token=None,
                        lease_until=None,
                        updated_at=time.time(),
                        error="Worker lease expired; resumable",
                    )
                )

    @contextmanager
    def lease(self, candidate, seconds=180):
        stopped, lost = threading.Event(), threading.Event()

        def renew():
            while not stopped.wait(max(0.1, min(seconds / 3, 30))):
                try:
                    self.update(
                        candidate["id"],
                        expected=candidate["status"],
                        claim_token=candidate["claim_token"],
                        lease_until=time.time() + seconds,
                    )
                except Exception:  # noqa: BLE001 -- loss of any heartbeat must fence the worker
                    lost.set()
                    return

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            yield lost
        finally:
            stopped.set()
            heartbeat.join(timeout=35)

    @contextmanager
    def lock(self, name, seconds=3600):
        owner, now = uuid.uuid4().hex, time.time()
        with self.connect(write=True) as c:
            c.execute(db.locks.delete().where(db.locks.c.until < now))
            result = c.execute(
                self.insert(db.locks)
                .values(name=name, owner=owner, until=now + seconds)
                .on_conflict_do_nothing(index_elements=["name"])
            )
            if result.rowcount != 1:
                raise LockBusy(f"Another worker is already handling {name}")
        stopped, lost = threading.Event(), threading.Event()

        def renew():
            while not stopped.wait(max(0.1, min(seconds / 3, 30))):
                try:
                    with self.connect(write=True) as c:
                        result = c.execute(
                            db.locks.update()
                            .where(
                                db.locks.c.name == name,
                                db.locks.c.owner == owner,
                                db.locks.c.until > time.time(),
                            )
                            .values(until=time.time() + seconds)
                        )
                        if result.rowcount != 1:
                            lost.set()
                            return
                except Exception:  # noqa: BLE001 -- fail closed on any renewal failure
                    lost.set()
                    return

        heartbeat = threading.Thread(target=renew, daemon=True)
        heartbeat.start()
        try:
            yield LockHandle(name, owner, lost)
        finally:
            stopped.set()
            heartbeat.join(timeout=35)
            with self.connect(write=True) as c:
                c.execute(db.locks.delete().where(db.locks.c.name == name, db.locks.c.owner == owner))

    def health(self, channel, **updates):
        with self.connect(write=True) as c:
            c.execute(
                self.insert(db.health)
                .values(channel=channel, updated_at=time.time(), detail="{}")
                .on_conflict_do_nothing(index_elements=["channel"])
            )
            row = (
                c.execute(self.row_lock(sa.select(db.health).where(db.health.c.channel == channel)))
                .mappings()
                .one()
            )
            detail = {**json.loads(row["detail"]), **updates}
            c.execute(
                db.health.update()
                .where(db.health.c.channel == channel)
                .values(updated_at=time.time(), detail=json.dumps(detail, allow_nan=False))
            )

    def snapshot(self):
        with self.connect() as c:
            counts = dict(
                c.execute(
                    sa.select(db.candidates.c.status, sa.func.count()).group_by(db.candidates.c.status)
                ).all()
            )
            health = [
                {"channel": row["channel"], "updated_at": row["updated_at"], **json.loads(row["detail"])}
                for row in c.execute(
                    sa.select(db.health).where(~db.health.c.channel.startswith("@service:"))
                ).mappings()
            ]
            events = [
                {**dict(row), "detail": json.loads(row["detail"])}
                for row in c.execute(
                    sa.select(db.events).order_by(db.events.c.id.desc()).limit(30)
                ).mappings()
            ]
        return {"counts": counts, "channels": health, "events": events, "candidates": self.list()}

    def reserve_publication(self, cid, guard, *, expected_version=None):
        """Persist the final-send reservation and its cadence timestamp together."""
        now = time.time()
        with self.connect(write=True) as c:
            self.assert_guard(c, guard)
            query = db.candidates.update().where(
                db.candidates.c.id == cid, db.candidates.c.status == "container_ready"
            )
            if expected_version is not None:
                query = query.where(db.candidates.c.updated_at == expected_version)
            result = c.execute(
                query.values(
                    status="publishing", updated_at=now, error=None, claim_token=None, lease_until=None
                )
            )
            if result.rowcount != 1:
                raise LeaseLost("Candidate changed before publication; reload it")
            c.execute(
                db.events.insert().values(at=now, kind="publication_attempt", candidate_id=cid, detail="{}")
            )

    def publication_window(self):
        # A lost response may have accepted a public post. Reserve its budget until
        # it ages out or reconciliation confirms publication; confirmed rows count
        # once. Old databases have no attempt event, so hold their last known time.
        attempts = (
            sa.select(db.events.c.candidate_id, sa.func.max(db.events.c.at).label("attempt_at"))
            .where(db.events.c.kind == "publication_attempt")
            .group_by(db.events.c.candidate_id)
            .subquery()
        )
        counted_at = sa.case(
            (
                db.candidates.c.status.in_(("publishing", "publish_unknown")),
                sa.func.coalesce(attempts.c.attempt_at, db.candidates.c.updated_at),
            ),
            else_=db.candidates.c.published_at,
        )
        with self.connect() as c:
            row = c.execute(
                sa.select(sa.func.count(), sa.func.max(counted_at))
                .select_from(db.candidates.outerjoin(attempts, attempts.c.candidate_id == db.candidates.c.id))
                .where(counted_at > time.time() - 86400)
            ).one()
        return row[0], row[1]

    def retry_publication(self, cid, guard, *, expected_version):
        """Requeue only a proven pre-send container failure, with an audit event."""
        now = time.time()
        with self.connect(write=True) as c:
            self.assert_guard(c, guard)
            item = self.decode(
                c.execute(self.row_lock(sa.select(db.candidates).where(db.candidates.c.id == cid)))
                .mappings()
                .first()
            )
            if item is None or item["updated_at"] != expected_version:
                raise LeaseLost("This clip changed; reload it before retrying publication")
            failure = item["metadata"].get("publication_failure") or {}
            attempted = c.execute(
                sa.select(db.events.c.id)
                .where(db.events.c.candidate_id == cid, db.events.c.kind == "publication_attempt")
                .limit(1)
            ).first()
            if (
                item["status"] != "publish_failed"
                or not isinstance(failure, dict)
                or failure.get("phase") != "container_processing"
                or failure.get("status") not in {"ERROR", "EXPIRED"}
                or not item["container_id"]
                or failure.get("container_id") != item["container_id"]
                or attempted
                or item["media_id"]
                or item["published_at"] is not None
            ):
                raise RelayError(
                    "Only a confirmed failure before the final publish request can be retried; "
                    "uncertain publication requires reconciliation"
                )
            metadata = {
                key: value
                for key, value in item["metadata"].items()
                if key not in {"publication_failure", "publication_errors"}
            }
            c.execute(
                db.candidates.update()
                .where(db.candidates.c.id == cid)
                .values(
                    status="approved",
                    updated_at=now,
                    container_id=None,
                    public_url=None,
                    error=None,
                    available_at=0,
                    claim_token=None,
                    lease_until=None,
                    metadata=json.dumps(metadata, allow_nan=False),
                )
            )
            c.execute(
                db.events.insert().values(
                    at=now,
                    kind="publication_retry_requested",
                    candidate_id=cid,
                    detail=json.dumps(
                        {"failed_container": failure, "output_hash": item["output_hash"]}, allow_nan=False
                    ),
                )
            )

    def recent_agent_calls(self):
        with self.connect() as c:
            return c.execute(
                sa.select(sa.func.count())
                .select_from(db.events)
                .where(db.events.c.kind == "agent_call", db.events.c.at > time.time() - 3600)
            ).scalar_one()

    def reserve_agent_call(self, limit, cid, stage):
        with self.connect(write=True) as c:
            if self.engine.dialect.name == "postgresql":
                c.execute(sa.text("SELECT pg_advisory_xact_lock(7265657)"))
            count = c.execute(
                sa.select(sa.func.count())
                .select_from(db.events)
                .where(db.events.c.kind == "agent_call", db.events.c.at > time.time() - 3600)
            ).scalar_one()
            if count >= limit:
                return False
            c.execute(
                db.events.insert().values(
                    kind="agent_call", at=time.time(), candidate_id=cid, detail=json.dumps({"stage": stage})
                )
            )
            return True

    def get_setting(self, key, default=None):
        with self.connect() as c:
            value = c.execute(sa.select(db.settings.c.value).where(db.settings.c.key == key)).scalar()
        return json.loads(value) if value is not None else default

    def set_setting(self, key, value):
        with self.connect(write=True) as c:
            values = {"key": key, "value": json.dumps(value, allow_nan=False), "updated_at": time.time()}
            c.execute(
                self.insert(db.settings)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["key"], set_={k: v for k, v in values.items() if k != "key"}
                )
            )

    def ensure_setting(self, key, default):
        with self.connect(write=True) as c:
            c.execute(
                self.insert(db.settings)
                .values(key=key, value=json.dumps(default), updated_at=time.time())
                .on_conflict_do_nothing(index_elements=["key"])
            )
            return json.loads(
                c.execute(sa.select(db.settings.c.value).where(db.settings.c.key == key)).scalar_one()
            )
