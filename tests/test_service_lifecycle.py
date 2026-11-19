"""Local service recovery and installation transactions, without external work."""

from __future__ import annotations

import copy
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from reelrelay import controls, install, supervisor
from reelrelay.config import DEFAULTS, load, save
from reelrelay.store import Store
from reelrelay.util import RelayError, read_json, write_json


class _Clock:
    now = 0

    def monotonic(self):
        return self.now


class _Stop:
    def __init__(self, clock, after, on_wait=None):
        self.clock, self.after, self.on_wait = clock, after, on_wait

    def wait(self, seconds):
        self.clock.now += seconds
        if self.on_wait:
            self.on_wait()
        return self.clock.now >= self.after


class _Child:
    """A group may outlive its process leader, just like an active renderer."""

    def __init__(self, pid, command, *, failed=False, stubborn=False):
        self.pid, self.command = pid, command
        self.returncode = 7 if failed else None
        self.stubborn = stubborn
        self.group_alive = True
        self.wait_calls = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired(self.command, timeout)
        return self.returncode


class SupervisorLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.clock = _Clock()
        self.guard = SimpleNamespace(lost=threading.Event())
        self.store = Mock()
        self.store.lock.return_value = nullcontext(self.guard)
        self.store.list.return_value = []
        self.children, self.starts, self.signals = [], [], []
        self.cfg = {"config_path": "/test/workspace/config.toml", "web": {"port": 8374}}
        self.args = SimpleNamespace(duration=None, port=None, open=False)

    def run_supervisor(self, *, after=2, lose_guard=False, fail_editors=0, stubborn_role=None, on_start=None):
        attempts = {}

        def start(command, **kwargs):
            self.assertEqual(kwargs, {"start_new_session": True})
            role = command[6]
            attempts[role] = attempts.get(role, 0) + 1
            child = _Child(
                8000 + len(self.children),
                command,
                failed=role == "worker" and attempts[role] <= fail_editors,
                stubborn=role == stubborn_role,
            )
            if on_start:
                on_start(child, attempts[role])
            self.children.append(child)
            self.starts.append((role, self.clock.now, child.pid))
            return child

        def killpg(pid, signum):
            # An unexpected PID would target a process this supervisor does not own.
            child = next(child for child in self.children if child.pid == pid)
            if not child.group_alive:
                raise ProcessLookupError(pid)
            self.signals.append((pid, signum))
            if signum == signal.SIGKILL or not child.stubborn:
                child.group_alive = False
                if child.returncode is None:
                    child.returncode = -signum

        stop = _Stop(self.clock, after, self.guard.lost.set if lose_guard else None)
        with (
            patch.object(supervisor, "Store", return_value=self.store),
            patch.object(supervisor, "service_enabled", return_value=True),
            patch.object(supervisor.subprocess, "Popen", side_effect=start),
            patch.object(supervisor.os, "killpg", side_effect=killpg),
            patch.object(supervisor.time, "monotonic", self.clock.monotonic),
            patch("builtins.print"),
        ):
            supervisor.supervise(self.cfg, self.args, stop, web=True)

    def test_guard_loss_terminates_only_owned_process_groups_and_fences_health(self):
        self.run_supervisor(after=10, lose_guard=True, stubborn_role="worker")
        owned = {child.pid for child in self.children}
        self.assertEqual({pid for pid, _ in self.signals}, owned)
        self.assertTrue(all(not child.group_alive for child in self.children))
        editor = next(child for child in self.children if child.command[6] == "worker")
        self.assertEqual(
            [signum for pid, signum in self.signals if pid == editor.pid],
            [signal.SIGINT, signal.SIGKILL],
        )
        self.assertEqual(editor.wait_calls, [25, None])
        self.assertFalse(
            any(call.kwargs.get("state") == "stopped" for call in self.store.health.call_args_list)
        )

    def test_failed_role_restarts_under_exponential_backoff_not_every_poll(self):
        self.run_supervisor(after=10, fail_editors=2)
        self.assertEqual([at for role, at, _ in self.starts if role == "worker"], [0, 3, 8])
        self.assertEqual([role for role, _, _ in self.starts].count("run"), 1)
        self.assertEqual([role for role, _, _ in self.starts].count("publisher"), 1)
        events = [call.args for call in self.store.event.call_args_list]
        self.assertEqual(
            events,
            [
                ("service_restart_scheduled", {"service": "editor", "exit_code": 7}),
                ("service_restart_scheduled", {"service": "editor", "exit_code": 7}),
            ],
        )
        stopped = {
            call.args[0] for call in self.store.health.call_args_list if call.kwargs.get("state") == "stopped"
        }
        self.assertEqual(stopped, {"@service:capture", "@service:editor", "@service:supervisor"})

    def test_failed_leader_descendants_are_stopped_before_replacement(self):
        def inspect_start(child, attempt):
            if child.command[6] == "worker" and attempt > 1:
                previous = [old for old in self.children if old.command[6] == "worker"][-1]
                self.assertFalse(
                    previous.group_alive, "A restarted worker must not leave its old renderer alive"
                )

        self.run_supervisor(after=5, fail_editors=1, stubborn_role="worker", on_start=inspect_start)
        self.assertTrue(all(not child.group_alive for child in self.children))

    def test_guard_loss_also_stops_descendants_of_an_already_exited_leader(self):
        self.run_supervisor(after=10, lose_guard=True, fail_editors=1, stubborn_role="worker")
        self.assertTrue(all(not child.group_alive for child in self.children))


class _Workspace(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config_path = self.root / "config.toml"
        # Tests never adopt a developer's external database via an environment override.
        environment = patch.dict(os.environ, {"REEL_RELAY_DATABASE_URL": ""})
        environment.start()
        self.addCleanup(environment.stop)
        values = copy.deepcopy(DEFAULTS)
        values.update(data_dir="runtime", reel_studio="unused-studio")
        save(self.config_path, values)
        self.cfg = load(self.config_path)
        self.data = Path(self.cfg["data_dir"])
        self.store = Store(self.cfg)

    def select_previous_install(self):
        self.old_model = self.data / "engine/models/small-previous"
        self.write_model(self.old_model)
        self.old_python = self.data / "engine/previous-environment/bin/python"
        self.old_python.parent.mkdir(parents=True)
        self.old_python.write_bytes(b"previous interpreter: do not replace")
        self.registry = self.data / "engine/models/installed.json"
        write_json(self.registry, {"small": self.old_model.name})
        self.cfg["media"].update(
            transcription_python=str(self.old_python.relative_to(self.data)),
            transcription_model=str(self.old_model.relative_to(self.data)),
        )
        save(self.config_path, self.cfg)
        self.config_before = self.config_path.read_bytes()
        self.old_bytes = {path.name: path.read_bytes() for path in self.old_model.iterdir()}

    @staticmethod
    def write_model(path, *, complete=True):
        path.mkdir(parents=True, exist_ok=True)
        (path / "model.bin").write_bytes(b"test model weights")
        if complete:
            write_json(path / "config.json", {"model_type": "whisper"})
            write_json(path / "tokenizer.json", {"version": "1.0"})

    def assert_previous_unchanged(self):
        self.assertEqual(self.old_python.read_bytes(), b"previous interpreter: do not replace")
        self.assertEqual({path.name: path.read_bytes() for path in self.old_model.iterdir()}, self.old_bytes)


class MediaInstallationLifecycleTests(_Workspace):
    def setUp(self):
        super().setUp()
        self.select_previous_install()
        self.commands = []

    def fake_runner(self, download):
        def run(command, **kwargs):
            self.commands.append(command)
            self.assertTrue(kwargs["check"])
            self.assertGreater(kwargs["timeout"], 0)
            if command[1:3] == ["-m", "venv"]:
                python = Path(command[3]) / "bin/python"
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_bytes(b"new locked environment")
            elif command[1:3] == ["-m", "pip"]:
                self.assertIn("--require-hashes", command)
            elif command[1] == "-c":
                self.assertIn("from faster_whisper.utils import download_model", command[2])
                self.assertEqual(self.config_path.read_bytes(), self.config_before)
                download(Path(command[-1]))
            else:
                self.fail(f"Unexpected external operation: {command!r}")
            return SimpleNamespace(returncode=0)

        return run

    def test_partial_download_stays_unselected_and_retry_promotes_complete_version(self):
        staging = self.data / "engine/models/.base-download"
        with (
            patch.object(
                install.subprocess,
                "run",
                side_effect=self.fake_runner(lambda path: self.write_model(path, complete=False)),
            ),
            self.assertRaisesRegex(RelayError, "did not complete"),
        ):
            install.setup_media(self.cfg, "base", download=True)
        self.assertEqual(self.config_path.read_bytes(), self.config_before)
        self.assertEqual(read_json(self.registry), {"small": "small-previous"})
        self.assertFalse(install.setup_media(self.cfg, "base")["model_installed"])
        self.assertTrue((staging / "model.bin").is_file())
        self.assert_previous_unchanged()

        def finish(path):
            self.assertEqual(path, staging)
            self.assertTrue((path / "model.bin").is_file(), "Retry must retain the resumable download")
            self.write_model(path)

        with patch.object(install.subprocess, "run", side_effect=self.fake_runner(finish)):
            result = install.setup_media(self.cfg, "base", download=True)
        self.assertTrue(result["model_installed"])
        selected = load(self.config_path)["media"]
        model = self.data / selected["transcription_model"]
        python = self.data / selected["transcription_python"]
        self.assertNotEqual(model, staging)
        self.assertTrue(model.name.startswith("base-"))
        self.assertTrue(install._model_complete(model))
        self.assertFalse(staging.exists())
        self.assertEqual(read_json(self.registry), {"small": "small-previous", "base": model.name})
        self.assertTrue(python.is_file())
        self.assertNotEqual(python, self.old_python)
        self.assertEqual(sum(command[1:3] == ["-m", "venv"] for command in self.commands), 1)
        self.assertEqual(sum(command[1:3] == ["-m", "pip"] for command in self.commands), 1)
        self.assert_previous_unchanged()

    def test_invalid_model_metadata_and_empty_weights_are_not_reported_installed(self):
        target = self.data / "engine/models/base"
        cases = ((b"", {}, {}), (b"weights", None, {}), (b"weights", [], {}), (b"weights", {}, []))
        for weights, config, tokenizer in cases:
            with self.subTest(weights=weights, config=config, tokenizer=tokenizer):
                target.mkdir(exist_ok=True)
                (target / "model.bin").write_bytes(weights)
                (target / "config.json").write_text("invalid json" if config is None else json.dumps(config))
                write_json(target / "tokenizer.json", tokenizer)
                with patch.object(install.subprocess, "run") as external:
                    self.assertFalse(install.setup_media(self.cfg, "base")["model_installed"])
                external.assert_not_called()
                self.assertEqual(self.config_path.read_bytes(), self.config_before)

    def test_lost_install_lease_never_promotes_or_selects_complete_staging(self):
        guard = SimpleNamespace(lost=threading.Event())
        fake_store = Mock()
        fake_store.lock.return_value = nullcontext(guard)

        def finish_then_lose(path):
            self.write_model(path)
            guard.lost.set()

        with (
            patch.object(install, "Store", return_value=fake_store),
            patch.object(install.subprocess, "run", side_effect=self.fake_runner(finish_then_lose)),
            self.assertRaisesRegex(RelayError, "ownership expired"),
        ):
            install.setup_media(self.cfg, "base", download=True)
        self.assertEqual(self.config_path.read_bytes(), self.config_before)
        self.assertEqual(read_json(self.registry), {"small": "small-previous"})
        self.assertTrue(install._model_complete(self.data / "engine/models/.base-download"))
        self.assert_previous_unchanged()

    def test_previously_complete_model_is_selected_without_redownloading(self):
        with patch.object(
            install.subprocess,
            "run",
            side_effect=self.fake_runner(
                lambda path: self.fail("A complete installed model must not be downloaded again")
            ),
        ):
            install.setup_media(self.cfg, "small", download=True)
        self.assertEqual(
            load(self.config_path)["media"]["transcription_model"], "engine/models/small-previous"
        )
        self.assertEqual(read_json(self.registry), {"small": "small-previous"})
        self.assert_previous_unchanged()

    def test_online_setup_uses_isolated_home_and_drops_account_credentials(self):
        sensitive = {
            "GEMINI_API_KEY",
            "HF_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
            "REEL_RELAY_ENCRYPTION_KEY",
            "REEL_RELAY_WEB_TOKEN",
            "REEL_RELAY_S3_SECRET_ACCESS_KEY",
            "CODEX_HOME",
            "PYTHONPATH",
            "PIP_INDEX_URL",
        }
        inherited = dict.fromkeys(sensitive, "test-only-sensitive-value")
        inherited.update(HOME="/test/unrelated-personal-home", HTTPS_PROXY="http://test-proxy:3128")
        with (
            patch.dict(os.environ, inherited),
            patch.object(
                install.subprocess, "run", side_effect=self.fake_runner(self.write_model)
            ) as external,
        ):
            install.setup_media(self.cfg, "base", download=True)
            self.assertEqual(os.environ["HOME"], inherited["HOME"])
        self.assertGreaterEqual(external.call_count, 3)
        for call in external.call_args_list:
            environment = call.kwargs["env"]
            self.assertTrue(sensitive.isdisjoint(environment))
            self.assertEqual(Path(environment["HOME"]), self.data / "engine/setup-home")
            self.assertEqual(environment["HF_HUB_DISABLE_IMPLICIT_TOKEN"], "1")
            self.assertEqual(environment["PYTHONNOUSERSITE"], "1")
            self.assertEqual(environment["PIP_CONFIG_FILE"], os.devnull)
            self.assertEqual(environment["HTTPS_PROXY"], "http://test-proxy:3128")


class InstallJobLifecycleTests(_Workspace):
    def request(self, model="small"):
        with patch.object(controls, "_launch", return_value=9876):
            controls.request_install(self.cfg, model)
        return self.store.get_setting("media_install")

    def test_failed_job_is_sanitized_and_a_new_request_can_succeed(self):
        first = self.request()
        with patch.object(
            install, "setup_media", side_effect=RelayError("secret-token must not enter status")
        ):
            controls.run_install_job(self.cfg, "small", first["id"])
        failed = controls.installation_status(self.cfg)
        self.assertEqual(failed["status"], "failed")
        self.assertNotIn("secret-token", json.dumps(failed))
        second = self.request()
        self.assertNotEqual(first["id"], second["id"])
        with patch.object(install, "setup_media", return_value={"model_installed": True}) as setup:
            controls.run_install_job(self.cfg, "small", second["id"])
        setup.assert_called_once_with(self.cfg, "small", download=True)
        self.assertEqual(controls.installation_status(self.cfg)["status"], "succeeded")
        self.assertEqual(controls.service_health(self.store, "install")["job_id"], second["id"])

    def test_failed_spawn_is_retryable_and_does_not_expose_exception(self):
        with (
            patch.object(controls, "_launch", side_effect=OSError("secret path or credential")),
            self.assertRaisesRegex(RelayError, "Could not start the local model installer"),
        ):
            controls.request_install(self.cfg, "base")
        status = controls.installation_status(self.cfg)
        self.assertEqual(status["status"], "failed")
        self.assertNotIn("secret", json.dumps(status))
        self.assertEqual(self.request("base")["status"], "running")

    def test_running_request_is_deduplicated_and_conflicting_model_is_rejected(self):
        active = self.request()
        with patch.object(controls, "_launch") as launch:
            self.assertEqual(controls.request_install(self.cfg, "small")["status"], "running")
            with self.assertRaisesRegex(RelayError, "current model installation"):
                controls.request_install(self.cfg, "base")
        launch.assert_not_called()
        self.assertEqual(self.store.get_setting("media_install"), active)

    def test_superseded_job_cannot_overwrite_new_request_or_its_health(self):
        first = self.request()
        replacement = {"id": "new-job", "status": "running", "model": "base", "started_at": time.time()}

        def supersede(*args, **kwargs):
            self.store.set_setting("media_install", replacement)
            self.store.health("@service:install", state="running", job_id="new-job")

        with patch.object(install, "setup_media", side_effect=supersede):
            controls.run_install_job(self.cfg, "small", first["id"])
        self.assertEqual(self.store.get_setting("media_install"), replacement)
        self.assertEqual(controls.service_health(self.store, "install")["job_id"], "new-job")

    def test_lost_job_guard_cannot_write_a_success_outcome(self):
        active = self.request()
        guard = SimpleNamespace(lost=threading.Event())
        original_lock = self.store.lock

        def lock(name, **kwargs):
            return nullcontext(guard) if name == "media-install" else original_lock(name, **kwargs)

        with (
            patch.object(controls, "Store", return_value=self.store),
            patch.object(self.store, "lock", side_effect=lock),
            patch.object(install, "setup_media", side_effect=lambda *args, **kwargs: guard.lost.set()),
        ):
            controls.run_install_job(self.cfg, "small", active["id"])
        self.assertEqual(self.store.get_setting("media_install"), active)
        self.assertNotEqual((controls.service_health(self.store, "install") or {}).get("state"), "succeeded")

    def test_foreign_heartbeat_cannot_make_an_abandoned_request_look_running(self):
        active = self.request()
        active["started_at"] = time.time() - 500
        self.store.set_setting("media_install", active)
        self.store.health("@service:install", state="running", job_id="unrelated-old-job")
        self.assertEqual(controls.installation_status(self.cfg)["status"], "failed")
        self.store.health("@service:install", state="running", job_id=active["id"])
        self.assertEqual(controls.installation_status(self.cfg)["status"], "running")

    def test_outdated_job_does_not_invoke_the_installer(self):
        active = self.request()
        with (
            patch.object(install, "setup_media") as setup,
            self.assertRaisesRegex(RelayError, "no longer active"),
        ):
            controls.run_install_job(self.cfg, "small", "stale-request")
        setup.assert_not_called()
        self.assertEqual(self.store.get_setting("media_install"), active)


if __name__ == "__main__":
    unittest.main()
