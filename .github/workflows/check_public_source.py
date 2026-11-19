"""Reject private artifacts and machine-specific paths in the Git index.

This complements a secret scanner; it never prints matching content. Run before
the first commit, before a release, and in CI. Only indexed files are inspected.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = {
    "runtime",
    "node_modules",
    ".venv",
    ".venv-release",
    "__pycache__",
    ".private",
    "secrets",
    ".aws",
    ".codex",
    ".agents",
    "dist",
    "build",
    ".cache",
}
FORBIDDEN_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".wav",
    ".mp3",
    ".ogg",
    ".flac",
    ".gguf",
    ".safetensors",
    ".bin",
    ".sqlite3",
    ".sqlite",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".pyc",
    ".log",
}
PERSONAL_PATH = re.compile(rb"/(?:Users|home)/[A-Za-z0-9_.-]+/")


def main():
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
    failures, checked = [], 0
    for name in filter(None, names):
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or FORBIDDEN_PARTS.intersection(path.parts)
            or path.suffix in FORBIDDEN_SUFFIXES
            or path.name == "config.toml"
            or path.name.startswith("config.toml.")
            or path.name == ".env"
            or path.name.startswith(".env.")
            or path.suffix == ".env"
            or (root / name).is_symlink()
        ):
            failures.append(name + ": private or unsafe artifact")
            continue
        data = subprocess.check_output(["git", "show", ":" + name], cwd=root)
        if len(data) > 3 * 1024**2:
            failures.append(name + ": unexpectedly large source artifact")
        if PERSONAL_PATH.search(data):
            failures.append(name + ": absolute home-directory path")
        checked += 1
    if not checked:
        raise SystemExit("No indexed source files found; refusing an empty publication check")
    if failures:
        raise SystemExit("Publication check failed:\n" + "\n".join(failures))
    print(f"Public source boundary passed for {checked} indexed files. Run a secret scanner separately.")


if __name__ == "__main__":
    main()
