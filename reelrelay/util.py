from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class RelayError(Exception):
    pass


class TransientError(RelayError):
    pass


class APIError(RelayError):
    def __init__(self, status, payload):
        self.status, self.payload = status, payload
        # Never echo an upstream message which could contain credentials or signed URLs.
        super().__init__(f"Remote service returned HTTP {status}")


def tls_context():
    context = ssl.create_default_context()
    try:
        import certifi

        context.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return context


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API endpoints must not forward credentials to a redirected destination.
        return None


def write_json(path, value, *, private=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
        temp = Path(f.name)
    if private:
        temp.chmod(0o600)
        path.parent.chmod(0o700)
    temp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise RelayError("File is outside the configured data directory")
    return path


def http_json(url, *, method="GET", headers=None, data=None, form=None, timeout=30, local_only=False):
    handlers = [NoRedirect()]
    if local_only:
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.fragment
        ):
            raise RelayError("Local requests require a loopback HTTP endpoint")
        # Avoid both ambient HTTP proxies and a modified localhost DNS mapping.
        host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
        try:
            port = parsed.port
        except ValueError as exc:
            raise RelayError("Invalid local endpoint port") from exc
        authority = host + (f":{port}" if port is not None else "")
        url = urllib.parse.urlunsplit(("http", authority, parsed.path, parsed.query, ""))
        handlers.append(urllib.request.ProxyHandler({}))
    else:
        handlers.append(urllib.request.HTTPSHandler(context=tls_context()))
    h = {"Accept": "application/json", "User-Agent": "ReelRelay/0.3", **(headers or {})}
    body = None
    if data is not None:
        body = json.dumps(data, allow_nan=False).encode()
        h["Content-Type"] = "application/json"
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        opener = urllib.request.build_opener(*handlers)
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                raise RelayError("JSON response exceeds the local size limit")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read(16384))
        except ValueError:
            payload = {}
        raise APIError(e.code, payload) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise TransientError("Network connection failed or timed out") from e


def run(args, *, timeout=300, cwd=None, env=None):
    result = subprocess.run(
        list(map(str, args)), cwd=cwd, env=env, text=True, capture_output=True, timeout=timeout, check=False
    )
    if result.returncode:
        raise RelayError(f"{Path(str(args[0])).name} failed: {result.stderr[-2000:]}")
    return result.stdout
