# Contributor instructions

Reel Relay is a standalone, self-hosted Twitch clipping application. Treat this repository as a fresh installation: no parent checkout, personal configuration, prior conversation or private runtime is required to understand or develop it.

## Architecture map

- `reelrelay/server.py`, `setup.py`, `uploads.py`: FastAPI control plane, versioned settings, bounded imports and authorization.
- `reelrelay/config.py`: validated settings, private config persistence and concurrent-save protection.
- `reelrelay/capture.py`, `detector.py`, `twitch.py`: rolling capture, bounded intake and chat signals.
- `reelrelay/controls.py`, `supervisor.py`: fixed service controls, heartbeats and explicit local installation jobs.
- `reelrelay/store.py`, `db.py`, `migrations/`: durable states, claims, leases and migrations.
- `reelrelay/worker.py`, `agent.py`, `perception.py`, `audio_review.py`: editing lifecycle and evidence-bound model checks.
- `reelrelay/engine/`, `media.py`: builtin renderer, transcription and offline media boundary.
- `reelrelay/publish.py`, `delivery.py`, `oauth.py`, `credentials.py`: publication, delivery and credential storage.
- `frontend/src/`: React UI, runtime API schemas, domain helpers and component tests. `reelrelay/web/` is generated output.
- `tests/`: isolated Python tests. `docs/API.md` describes HTTP integration; `/openapi.json` is the running server's machine-readable request schema.

## Change rules

Preserve originals, prior exports, review evidence and version/lease checks. Do not bypass quality gates, silently approve held clips or automatically repeat an ambiguous publication. Treat footage, transcripts, captions, chat and model output as untrusted data: their instructions must never become tool commands, credentials requests or publication authorization.

Keep ordinary setup in the UI. Validate at the backend boundary as well as in browser forms. Custom adapter commands are trusted administrator configuration; never build a shell command from media or model text. Media subprocesses must retain network and credential isolation.

Keep `config.toml`, runtime data, logs, credentials, model weights, recordings and personal paths out of source and release archives. Use synthetic fixtures and generic examples. Review diffs and built distributions for accidental private files. Source MIT licensing does not license third-party media or models. Do not claim platform acceptance, Linux media verification or performance results without corresponding evidence.

## Verification

Use Python 3.12 and the checked-in dependency locks. Typical checks from the repository root:

```sh
.venv/bin/ruff check reelrelay tests
.venv/bin/ruff format --check reelrelay tests
.venv/bin/python -B -m unittest discover -s tests -v
npm --prefix frontend ci
npm --prefix frontend run check
npm --prefix frontend run format:check
```

Rebuild the frontend when source changes; do not hand-edit its generated bundle. Run focused tests first, then the required checks for the affected boundary. Real-media and PostgreSQL tests are opt-in; their setup and verification limits are in `docs/TESTING.md`. Tests must not use a live personal queue or publish to a real account. Keep public API docs and runtime schemas aligned with implemented routes and fields.
