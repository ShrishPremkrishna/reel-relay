"""SQLAlchemy connections and versioned migrations for local and server deployments."""

from __future__ import annotations

import atexit
import os
import threading
from functools import lru_cache
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from .util import RelayError

metadata = sa.MetaData()
candidates = sa.Table(
    "candidates",
    metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("channel", sa.String(64), nullable=False),
    sa.Column("status", sa.String(40), nullable=False),
    sa.Column("created_at", sa.Float, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
    sa.Column("session_id", sa.String(64)),
    sa.Column("start", sa.Float),
    sa.Column("end", sa.Float),
    *[
        sa.Column(name, sa.Text)
        for name in (
            "source_path",
            "project_path",
            "evidence_path",
            "decision",
            "caption",
            "output_path",
            "review",
            "public_url",
            "permalink",
            "error",
        )
    ],
    sa.Column("source_hash", sa.String(64), unique=True),
    sa.Column("output_hash", sa.String(64)),
    sa.Column("score", sa.Float),
    sa.Column("confidence", sa.Float),
    sa.Column("container_id", sa.String(128)),
    sa.Column("media_id", sa.String(128)),
    sa.Column("published_at", sa.Float),
    sa.Column("lease_until", sa.Float),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("is_test", sa.Integer, nullable=False, server_default="0"),
    sa.Column("attribution", sa.Text, nullable=False, server_default=""),
    sa.Column("metadata", sa.Text, nullable=False, server_default="{}"),
    sa.Column("claim_token", sa.String(64)),
    sa.Column("available_at", sa.Float, nullable=False, server_default="0"),
    sa.Column("publishing_account_id", sa.String(128)),
    sa.Index("work_queue", "status", "created_at"),
    sa.Index("available_work", "status", "available_at", "created_at"),
)
events = sa.Table(
    "events",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("at", sa.Float, nullable=False),
    sa.Column("kind", sa.String(64), nullable=False),
    sa.Column("candidate_id", sa.String(64)),
    sa.Column("detail", sa.Text, nullable=False),
)
health = sa.Table(
    "health",
    metadata,
    sa.Column("channel", sa.String(128), primary_key=True),
    sa.Column("updated_at", sa.Float, nullable=False),
    sa.Column("detail", sa.Text, nullable=False),
)
locks = sa.Table(
    "locks",
    metadata,
    sa.Column("name", sa.String(128), primary_key=True),
    sa.Column("owner", sa.String(64), nullable=False),
    sa.Column("until", sa.Float, nullable=False),
)
settings = sa.Table(
    "settings",
    metadata,
    sa.Column("key", sa.String(128), primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
)
vault = sa.Table(
    "vault",
    metadata,
    sa.Column("name", sa.String(128), primary_key=True),
    sa.Column("ciphertext", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Float, nullable=False),
)
oauth_flows = sa.Table(
    "oauth_flows",
    metadata,
    sa.Column("state_hash", sa.String(64), primary_key=True),
    sa.Column("provider", sa.String(32), nullable=False),
    sa.Column("expires_at", sa.Float, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
)

_setup_lock = threading.Lock()
_initialized = set()


def dispose_connections():
    for engine in list(_initialized):
        engine.dispose()
    _initialized.clear()
    engine_for.cache_clear()


def after_fork():
    # Never let a prefork server share a DBAPI socket with its parent. This is
    # SQLAlchemy's documented child-process pool replacement strategy.
    global _setup_lock
    _setup_lock = threading.Lock()
    for engine in list(_initialized):
        engine.dispose(close=False)


atexit.register(dispose_connections)
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=after_fork)


def url_for(root, database_url=None):
    url = database_url or os.environ.get("REEL_RELAY_DATABASE_URL")
    if not url:
        return sa.URL.create("sqlite", database=str(Path(root).resolve() / "queue.sqlite3"))
    try:
        parsed = sa.make_url(url)
    except sa.exc.ArgumentError:
        raise RelayError("Use a PostgreSQL database URL or leave it blank for managed local SQLite") from None
    if parsed.get_backend_name() != "postgresql":
        raise RelayError(
            "Leave database_url blank for managed local SQLite, or use PostgreSQL; "
            "custom SQLite paths cannot be isolated safely from media workers"
        )
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed


@lru_cache(maxsize=32)
def engine_for(url) -> Engine:
    options = {"pool_pre_ping": True}
    if url.get_backend_name() == "sqlite":
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    engine = sa.create_engine(url, **options)
    if engine.dialect.name == "sqlite":

        @sa.event.listens_for(engine, "connect")
        def pragmas(connection, _):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


def migrate(engine):
    """One migration transaction, guarded across processes on each supported DB."""
    if engine in _initialized:
        return
    with _setup_lock, engine.connect() as connection:
        if engine in _initialized:
            return
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
            connection.begin()
            connection.execute(sa.text("SELECT pg_advisory_xact_lock(7265656)"))
        try:
            cfg = Config()
            cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
            cfg.attributes["connection"] = connection
            inspector = sa.inspect(connection)
            # Import the unversioned prototype database without touching its media.
            if inspector.has_table("candidates") and not inspector.has_table("alembic_version"):
                names = {c["name"] for c in inspector.get_columns("candidates")}
                prototype_columns = set(candidates.c.keys()) - {
                    "claim_token",
                    "available_at",
                    "publishing_account_id",
                }
                if names != prototype_columns or not all(
                    inspector.has_table(name) for name in ("events", "health", "locks")
                ):
                    raise RelayError("Unrecognized database schema; refusing an automatic migration")
                command.stamp(cfg, "0001")
            command.upgrade(cfg, "head")
            connection.commit()
            _initialized.add(engine)
        except BaseException:
            connection.rollback()
            raise
