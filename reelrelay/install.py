"""Explicit online setup; ordinary media processing never installs or downloads."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path

from .config import mutate
from .store import Store
from .util import RelayError, inside, read_json, write_json


def _model_complete(path):
    if not (path / "model.bin").is_file() or (path / "model.bin").stat().st_size == 0:
        return False
    try:
        return all(isinstance(read_json(path / name), dict) for name in ("config.json", "tokenizer.json"))
    except (OSError, ValueError):
        return False


def _installer_environment(root):
    home = root / "setup-home"
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        in {
            "PATH",
            "LANG",
            "LC_ALL",
            "TMPDIR",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "CURL_CA_BUNDLE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        }
    }
    environment.update(
        HOME=str(home),
        HF_HOME=str(home / "huggingface"),
        HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
        PYTHONNOUSERSITE="1",
        PIP_CONFIG_FILE=os.devnull,
        PIP_DISABLE_PIP_VERSION_CHECK="1",
    )
    return environment


def setup_media(cfg, model="small", *, download=False):
    root = inside(Path(cfg["data_dir"]) / "engine", cfg["data_dir"])
    lock = Path(__file__).parent / "engine" / "requirements.lock"
    if not lock.is_file():
        raise RelayError("This distribution is missing the locked transcription dependencies")
    fingerprint = hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
    environment = (
        root
        / "asr-envs"
        / f"{fingerprint}-{sys.version_info.major}{sys.version_info.minor}-{sys.platform}-{platform.machine()}"
    )
    python = environment / "bin/python"
    registry = root / "models" / "installed.json"
    installed = read_json(registry) if registry.is_file() else {}
    model_path = inside(root / "models" / installed.get(model, model), cfg["data_dir"])
    if not download:
        configured = cfg["media"].get("transcription_python")
        installed_python = Path(cfg["data_dir"]) / configured if configured else python
        return {
            "runtime_installed": installed_python.is_file(),
            "model_installed": _model_complete(model_path),
            "next_step": "reel-relay setup-media --download-model " + model,
            "notice": "The explicit download step installs locked Python packages and a local Whisper model; no footage is uploaded.",
        }
    if model not in {"tiny", "base", "small", "medium", "large-v3-turbo"}:
        raise RelayError("Choose a supported local Whisper model")
    root.mkdir(parents=True, exist_ok=True)
    installer_env = _installer_environment(root)
    # A changed dependency lock creates a different environment. Never modify the
    # interpreter used by an already running transcription job.
    with Store(cfg).lock("media-setup", seconds=90) as guard:
        marker = environment / ".installed.json"
        if not python.is_file() or not marker.is_file():
            subprocess.run(
                [sys.executable, "-m", "venv", str(environment)], check=True, timeout=120, env=installer_env
            )
            subprocess.run(
                [str(python), "-m", "pip", "install", "--require-hashes", "-r", str(lock)],
                check=True,
                timeout=900,
                env=installer_env,
            )
            marker.write_text(
                json.dumps({"requirements_sha256": hashlib.sha256(lock.read_bytes()).hexdigest()}) + "\n"
            )
        if not _model_complete(model_path):
            # Resume an interrupted download without touching an installed model.
            staging = inside(root / "models" / ("." + model + "-download"), cfg["data_dir"])
            staging.mkdir(parents=True, exist_ok=True)
            script = "from faster_whisper.utils import download_model; import sys; download_model(sys.argv[1], output_dir=sys.argv[2])"
            subprocess.run(
                [str(python), "-c", script, model, str(staging)], check=True, timeout=1800, env=installer_env
            )
            if not _model_complete(staging):
                raise RelayError("Model installation did not complete")
            if guard.lost.is_set():
                raise RelayError("Installer ownership expired; retry installation")
            model_path = staging.with_name(model + "-" + uuid.uuid4().hex[:12])
            staging.rename(model_path)
            installed = read_json(registry) if registry.is_file() else {}
            installed[model] = model_path.name
            write_json(registry, installed)
        if guard.lost.is_set():
            raise RelayError("Installer ownership expired; retry installation")

    def change(latest):
        latest["media"].update(
            engine="builtin",
            transcription_python=str(python.relative_to(cfg["data_dir"])),
            transcription_model=str(model_path.relative_to(cfg["data_dir"])),
        )

    mutate(cfg["config_path"], change)
    result = {"runtime_installed": True, "model_installed": True, "model": model, "engine": "builtin"}
    (root / "installation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
