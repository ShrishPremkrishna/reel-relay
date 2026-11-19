"""Verify release contents and smoke-test a wheel away from its source checkout.

No dependency resolution, model download, media ingestion, platform login or
publication occurs here. Run after `python -m build --no-isolation`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

WHEEL_FILES = {
    "reelrelay/config.example.toml",
    "reelrelay/engine/__init__.py",
    "reelrelay/engine/backend.py",
    "reelrelay/engine/asr_runner.py",
    "reelrelay/engine/captions.py",
    "reelrelay/engine/render.py",
    "reelrelay/engine/requirements.lock",
    "reelrelay/migrations/env.py",
    "reelrelay/web/index.html",
    "reelrelay/web/setup.html",
}
SOURCE_FILES = {
    "AGENTS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "bootstrap.sh",
    "README.md",
    "config.example.toml",
    "requirements.lock",
    "requirements-server.lock",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/.prettierrc.json",
    "frontend/tsconfig.json",
    "frontend/vite.config.ts",
    "frontend/src/main.tsx",
    "frontend/src/App.tsx",
    "frontend/src/hooks/useWorkspace.test.tsx",
    "frontend/src/styles/tokens.css",
    ".github/workflows/verify_distribution.py",
    ".github/workflows/test.yml",
    ".github/workflows/check_public_source.py",
    "docs/LOCAL_MODELS.md",
    *WHEEL_FILES,
}


def check_names(names):
    forbidden_dirs = {
        "runtime",
        "node_modules",
        ".venv",
        ".venv-release",
        "__pycache__",
        ".private",
        "secrets",
    }
    forbidden_extensions = {
        ".mp4",
        ".mov",
        ".wav",
        ".mp3",
        ".gguf",
        ".safetensors",
        ".bin",
        ".sqlite3",
        ".sqlite",
        ".pem",
        ".key",
        ".pyc",
    }
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise AssertionError(f"Unsafe archive member: {name}")
        if forbidden_dirs.intersection(path.parts) or path.suffix in forbidden_extensions:
            raise AssertionError(f"Private/runtime artifact in distribution: {name}")
        if path.name == "config.toml" or path.name == ".env" or path.name.startswith(".env."):
            raise AssertionError(f"Private configuration in distribution: {name}")


def check_wheel(path):
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        check_names(names)
        missing = WHEEL_FILES - names
        if missing:
            raise AssertionError(f"Wheel missing required files: {sorted(missing)}")
        html = archive.read("reelrelay/web/index.html").decode()
        if archive.read("reelrelay/web/setup.html").decode() != html:
            raise AssertionError("Setup and main application shells differ")
        assets = re.findall(r'(?:src|href)="(/assets/[^" ]+)"', html)
        if not assets or not any(asset.endswith(".js") for asset in assets):
            raise AssertionError("Wheel does not contain a compiled frontend entry")
        for asset in assets:
            if "reelrelay/web" + asset not in names:
                raise AssertionError(f"HTML refers to missing wheel asset: {asset}")
        cfg = tomllib.loads(archive.read("reelrelay/config.example.toml").decode())
        if cfg["media"]["engine"] != "builtin" or cfg["database_url"]:
            raise AssertionError("Fresh installations must use builtin media and managed SQLite")


def verify(folder):
    wheels, sources = sorted(folder.glob("*.whl")), sorted(folder.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise AssertionError("Use a clean output folder containing one wheel and one source distribution")
    check_wheel(wheels[0])
    with tempfile.TemporaryDirectory(prefix="reel-relay-package-") as directory:
        temporary = Path(directory)
        with tarfile.open(sources[0], "r:gz") as archive:
            members = archive.getmembers()
            roots = {PurePosixPath(member.name).parts[0] for member in members}
            if len(roots) != 1:
                raise AssertionError("Source distribution must have one root directory")
            names = {str(PurePosixPath(member.name).relative_to(next(iter(roots)))) for member in members}
            check_names(names)
            missing = SOURCE_FILES - names
            if missing:
                raise AssertionError(f"Source distribution missing required files: {sorted(missing)}")
            archive.extractall(temporary / "source", filter="data")
        source = temporary / "source" / next(iter(roots))
        if (source / "config.example.toml").read_bytes() != (
            source / "reelrelay/config.example.toml"
        ).read_bytes():
            raise AssertionError("Source and installed configuration examples must match")
        rebuilt = temporary / "rebuilt"
        subprocess.run(
            [sys.executable, "-m", "build", "--wheel", "--no-isolation", "--outdir", str(rebuilt)],
            cwd=source,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        rebuilt_wheels = list(rebuilt.glob("*.whl"))
        if len(rebuilt_wheels) != 1:
            raise AssertionError("Rebuilding the source distribution did not produce one wheel")
        check_wheel(rebuilt_wheels[0])
        installed = temporary / "installed"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-index",
                "--no-cache-dir",
                "--disable-pip-version-check",
                "--target",
                str(installed),
                str(rebuilt_wheels[0]),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        workspace = temporary / "workspace"
        workspace.mkdir()
        # Do not inherit real account, database, proxy or cloud credentials into
        # the isolated smoke workspace. Dependencies come from this Python env.
        environment = {
            key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "SYSTEMROOT"}
        }
        environment["PYTHONPATH"] = str(installed)
        location = subprocess.check_output(
            [sys.executable, "-c", "import reelrelay; print(reelrelay.__file__)"],
            cwd=workspace,
            env=environment,
            text=True,
        ).strip()
        if not Path(location).resolve().is_relative_to(installed.resolve()):
            raise AssertionError("Smoke test imported the source checkout instead of the installed wheel")
        base = [sys.executable, "-m", "reelrelay", "--config", str(workspace / "config.toml")]
        subprocess.run(base + ["init"], cwd=workspace, env=environment, check=True, stdout=subprocess.DEVNULL)
        report = json.loads(
            subprocess.check_output(base + ["doctor"], cwd=workspace, env=environment, text=True)
        )
        if (
            report["media_engine"] != "builtin"
            or report["database"] != "sqlite"
            or report["publishing_enabled"]
        ):
            raise AssertionError("Installed wheel does not start with safe standalone defaults")
        setup = json.loads(
            subprocess.check_output(base + ["setup-media"], cwd=workspace, env=environment, text=True)
        )
        if setup["runtime_installed"] or setup["model_installed"]:
            raise AssertionError(
                "Fresh wheel unexpectedly depends on a pre-existing transcription installation"
            )
    print(
        "Distribution verified: standalone engine/lock, compiled UI, rebuildable frontend source, isolated wheel startup."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    verify(parser.parse_args().folder.resolve())
