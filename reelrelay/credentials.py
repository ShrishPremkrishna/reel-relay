"""Encrypted credentials. Media and editorial workers never receive these values."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
import time
from pathlib import Path

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from . import db
from .store import Store
from .util import RelayError


def _cipher_from_key(key):
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (TypeError, ValueError):
        raise RelayError(
            "Credential encryption key is invalid; restore its backup before reconnecting accounts"
        ) from None


def _create_key(path):
    key = Fernet.generate_key()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".master-key-", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            os.fchmod(file.fileno(), 0o600)
            file.write(key)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return key


class Vault:
    def __init__(self, cfg):
        self.store = Store(cfg)
        key = os.environ.get("REEL_RELAY_ENCRYPTION_KEY")
        if key is not None:
            self.cipher = _cipher_from_key(key)
            return
        directory = Path(cfg["data_dir"]) / ".private"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        path = directory / "master.key"
        descriptor = os.open(directory / "master.key.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "a+b") as lock:
            os.fchmod(lock.fileno(), 0o600)
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    mode = path.lstat().st_mode
                except FileNotFoundError:
                    # Publish only a complete, durable key. A crashed creator can
                    # leave an unused temporary file, never an empty master key.
                    key_bytes = _create_key(path)
                else:
                    if not stat.S_ISREG(mode):
                        raise RelayError("Credential encryption key must be a regular private file")
                    key_bytes = path.read_bytes()
                self.cipher = _cipher_from_key(key_bytes)
                path.chmod(0o600)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def validate_name(name):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise ValueError("Invalid credential identifier")

    def put(self, name, value):
        self.validate_name(name)
        encrypted = self.cipher.encrypt(json.dumps(value, allow_nan=False).encode()).decode()
        values = {"name": name, "ciphertext": encrypted, "updated_at": time.time()}
        with self.store.connect(write=True) as c:
            c.execute(
                self.store.insert(db.vault)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=["name"], set_={k: v for k, v in values.items() if k != "name"}
                )
            )

    def get(self, name):
        self.validate_name(name)
        with self.store.connect() as c:
            value = c.execute(sa.select(db.vault.c.ciphertext).where(db.vault.c.name == name)).scalar()
        if value is None:
            return None
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except InvalidToken:
            raise RelayError(
                "Credentials cannot be decrypted with this server's key; restore the matching key"
            ) from None

    def exists(self, name):
        with self.store.connect() as c:
            return c.execute(sa.select(db.vault.c.name).where(db.vault.c.name == name)).scalar() is not None

    def remove(self, name):
        self.validate_name(name)
        with self.store.connect(write=True) as c:
            c.execute(db.vault.delete().where(db.vault.c.name == name))

    def account_status(self, name):
        record = self.get(name)
        if not record:
            return {"connected": False}
        expiry = record.get("expires_at")
        return {
            "connected": True,
            "username": record.get("username") or record.get("login"),
            "expires_at": expiry,
            "expired": bool(expiry and expiry <= time.time()),
            "account_id": record.get("account_id") or record.get("user_id"),
            "scopes": record.get("scopes", []),
        }
