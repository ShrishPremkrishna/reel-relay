# Deployment

The builtin engine is standalone. The local media path has been exercised on macOS. Linux media isolation requires bubblewrap and has not yet received end-to-end media verification. Windows is not supported.

## One local installation

After `./bootstrap.sh`, run `./relay start --open`. The default listener is `127.0.0.1:8765`. Configuration is in private `config.toml`; the managed database, credentials, artifacts and model installations live under the configured data directory, normally `runtime/`.

Use setup for sources, providers, transcription, editing preferences, accounts, delivery and pacing. The UI does not provision an operating-system service or cloud resources. For unattended operation, use an existing process manager, keep the machine available and arrange private log rotation and backups.

## HTTPS access and signed delivery

Use a stable HTTPS origin and reverse proxy you control. The upstream can stay on loopback:

```toml
[web]
host = "127.0.0.1"
port = 8765
public_base_url = "https://relay.example.com"
```

Set a dedicated `REEL_RELAY_WEB_TOKEN` of at least 32 random characters in the service's private environment. Do not put it in a URL or reuse a platform password. The UI exchanges it for a secure HttpOnly cookie. Editing the public origin in setup requires a server restart to apply the authentication/origin boundary.

The proxy must preserve the configured host and support streaming MP4 responses, HEAD and byte ranges. Avoid caching private API responses or logging query strings. Restrict the upstream to the proxy/loopback. `deploy/Caddyfile.example` is a starting point; DNS, certificates and the actual hostname are external setup.

Do not remove authentication from `/api/` or `/media/`. Public static UI does not expose workspace data. Signed `/delivery/` links temporarily serve an eligible reviewed export without exposing the queue. The configured HTTPS callback must exactly match the one registered with the platform.

## S3-compatible delivery

The delivery form supports an existing bucket, region, optional endpoint, public media base URL and dedicated credentials. Environment equivalents are `REEL_RELAY_S3_ACCESS_KEY_ID` and `REEL_RELAY_S3_SECRET_ACCESS_KEY`. Ambient personal AWS profiles are not read automatically.

Use a dedicated export destination whose HTTPS URLs Instagram can fetch. The application uploads final exports; it does not create buckets, modify public-access policies or provide distributed storage for intermediate editing artifacts. S3 delivery still needs a separate HTTPS sign-in callback.

Remote objects are not automatically deleted. Configure lifecycle expiration for the export prefix after a retention interval appropriate for publication and troubleshooting. Include noncurrent versions or incomplete multipart uploads if those features are in use. Keep unrelated data outside the scope of that policy.

## PostgreSQL and multiple workers

A blank `database_url` uses managed SQLite at `data_dir/queue.sqlite3`. Custom SQLite URLs are rejected to preserve the media boundary. Use local SQLite for one machine; do not place it on a network filesystem.

For PostgreSQL, install the corresponding dependency extra and provide a dedicated `REEL_RELAY_DATABASE_URL` privately. Remote database transport and role permissions are the administrator's responsibility. All workers need:

- The same compatible database schema and application version.
- The same artifact contents under a coordinated data root.
- The same credential encryption key, supplied privately.
- Compatible configuration and a deliberate way to distribute changes.

Database claims alone do not make media processing portable. Test shared-filesystem locks and artifact consistency before using multiple hosts. Older absolute-path artifacts and optional legacy projects may require migration or their original mount layout. S3 publishing support is not a replacement for shared working files.

## Control-plane container

The Dockerfile builds a web control-plane image, not a complete rendering stack. It does not install transcription weights, an Ollama service or all system media prerequisites.

```sh
.venv/bin/python -m build --no-isolation
docker build -t reel-relay-control:local .
```

Mount a writable configuration directory at `/config` and the private data directory at `/data`. A directory mount is needed because preference saves replace the config file atomically. Set `data_dir = "/data"`, the proper host/public origin and a private server token. Keep the container behind HTTPS rather than exposing its plain HTTP listener publicly.

The included Compose file is a template for private control-plane/database testing. It requires explicit credentials and prepared mounts; it does not start a full renderer or create public hosting. Check container UID/GID and mount permissions on the actual host without changing ownership of unrelated directories.

Back up the database, media, configuration and matching encryption key. Containers are replaceable; their private data and key are not. See [Operations](OPERATIONS.md) for restoration and upgrades.
