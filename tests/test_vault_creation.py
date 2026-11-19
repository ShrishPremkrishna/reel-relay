import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from reelrelay.credentials import Vault
from reelrelay.util import RelayError


def _concurrent_open(directory, barrier, results):
    with patch("reelrelay.credentials.Store", return_value=None):
        barrier.wait(timeout=15)
        vault = Vault({"data_dir": directory})
        results.put(vault.cipher.encrypt(b"concurrent-vault-fixture"))


def _crash_before_key_replace(directory):
    with (
        patch("reelrelay.credentials.Store", return_value=None),
        patch("reelrelay.credentials.os.replace", side_effect=lambda *_: os._exit(73)),
    ):
        Vault({"data_dir": directory})


class VaultCreationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.cfg = {"data_dir": str(self.directory)}
        self.key_path = self.directory / ".private" / "master.key"
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        store = patch("reelrelay.credentials.Store", return_value=None)
        store.start()
        self.addCleanup(store.stop)

    def test_concurrent_workers_share_one_private_key(self):
        context = multiprocessing.get_context("spawn")
        barrier, results = context.Barrier(4), context.Queue()
        processes = [
            context.Process(target=_concurrent_open, args=(str(self.directory), barrier, results))
            for _ in range(4)
        ]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(process.exitcode, 0)
            cipher = Vault(self.cfg).cipher
            for _ in processes:
                self.assertEqual(cipher.decrypt(results.get(timeout=3)), b"concurrent-vault-fixture")
            self.assertEqual(self.key_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.key_path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.key_path.parent / "master.key.lock").stat().st_mode & 0o777, 0o600)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                if process.pid is not None:
                    process.join(timeout=5)
            results.close()
            results.join_thread()

    def test_creator_crash_before_replace_leaves_no_partial_master_key(self):
        process = multiprocessing.get_context("spawn").Process(
            target=_crash_before_key_replace, args=(str(self.directory),)
        )
        process.start()
        try:
            process.join(timeout=15)
            self.assertEqual(process.exitcode, 73)
            self.assertFalse(self.key_path.exists())
            abandoned = list(self.key_path.parent.glob(".master-key-*"))
            self.assertEqual(len(abandoned), 1)
            self.assertEqual(abandoned[0].stat().st_mode & 0o777, 0o600)
            first = Vault(self.cfg)
            token = first.cipher.encrypt(b"recovered-after-creator-crash")
            self.assertEqual(Vault(self.cfg).cipher.decrypt(token), b"recovered-after-creator-crash")
            self.assertEqual(self.key_path.stat().st_mode & 0o777, 0o600)
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    def test_failed_write_does_not_publish_a_key_and_can_be_retried(self):
        with (
            patch("reelrelay.credentials.os.fsync", side_effect=OSError("simulated failed write")),
            self.assertRaises(OSError),
        ):
            Vault(self.cfg)
        self.assertFalse(self.key_path.exists())
        self.assertEqual(list(self.key_path.parent.glob(".master-key-*")), [])
        self.assertEqual(Vault(self.cfg).cipher.decrypt(Vault(self.cfg).cipher.encrypt(b"retry")), b"retry")

    def test_existing_invalid_key_is_never_replaced(self):
        self.key_path.parent.mkdir(mode=0o700)
        for invalid in (b"", b"invalid-key-fixture", b"x" * 44):
            with self.subTest(size=len(invalid)):
                self.key_path.write_bytes(invalid)
                with patch("reelrelay.credentials.Fernet.generate_key") as generate:
                    with self.assertRaisesRegex(RelayError, "Credential encryption key is invalid"):
                        Vault(self.cfg)
                    generate.assert_not_called()
                self.assertEqual(self.key_path.read_bytes(), invalid)

    def test_existing_valid_key_is_preserved_and_private(self):
        self.key_path.parent.mkdir(mode=0o700)
        key = Fernet.generate_key()
        self.key_path.write_bytes(key)
        self.key_path.chmod(0o644)
        with patch("reelrelay.credentials.Fernet.generate_key") as generate:
            vault = Vault(self.cfg)
            generate.assert_not_called()
        self.assertEqual(Fernet(key).decrypt(vault.cipher.encrypt(b"existing-key")), b"existing-key")
        self.assertEqual(self.key_path.read_bytes(), key)
        self.assertEqual(self.key_path.stat().st_mode & 0o777, 0o600)

    def test_invalid_environment_key_is_sanitized_without_local_fallback(self):
        for invalid in ("", "synthetic-invalid-master-key-value", "é" * 44):
            with (
                self.subTest(size=len(invalid)),
                patch.dict(os.environ, {"REEL_RELAY_ENCRYPTION_KEY": invalid}),
            ):
                with self.assertRaises(RelayError) as caught:
                    Vault(self.cfg)
                self.assertEqual(
                    str(caught.exception),
                    "Credential encryption key is invalid; restore its backup before reconnecting accounts",
                )
                self.assertTrue(caught.exception.__suppress_context__)
                self.assertFalse(self.key_path.parent.exists())

    def test_valid_environment_key_does_not_create_a_local_key(self):
        key = Fernet.generate_key()
        with patch.dict(os.environ, {"REEL_RELAY_ENCRYPTION_KEY": key.decode()}):
            vault = Vault(self.cfg)
        self.assertEqual(Fernet(key).decrypt(vault.cipher.encrypt(b"environment-key")), b"environment-key")
        self.assertFalse(self.key_path.parent.exists())
