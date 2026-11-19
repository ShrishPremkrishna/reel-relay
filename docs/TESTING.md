# Testing and verification limits

Tests should use temporary data roots, synthetic fixtures and mocked external services. Never point automated tests at an active personal queue, real account or non-disposable database. A passing simulated publication test is not evidence that an Instagram account is configured correctly.

## Standard checks

Use Python 3.12 and the checked-in dependency locks:

```sh
.venv/bin/ruff check reelrelay tests
.venv/bin/ruff format --check reelrelay tests
.venv/bin/python -B -m unittest discover -s tests -v
npm --prefix frontend ci
npm --prefix frontend run check
npm --prefix frontend run format:check
```

Python tests cover queue ownership, state transitions, API validation, provider/evidence contracts, credentials, delivery and publication recovery. Frontend tests use mocked API responses to check forms, conflict handling, review actions, source comparison, pagination, login and polling. They do not publish media.

The frontend build updates committed assets under `reelrelay/web/`. Check that generated files match their source before packaging.

## Real-media tests

Media integration tests are opt-in and require FFmpeg/FFprobe and the supported media sandbox:

```sh
REEL_RELAY_MEDIA_TESTS=1 .venv/bin/python -B -m unittest discover -s tests -v
```

Read the selected tests' prerequisites before enabling them. Do not disable sandboxing to make them pass. Model installation is a separate explicit network operation, not a normal rendering side effect.

The local media path has been exercised on macOS. Linux policy/command construction is covered, but end-to-end media verification on Linux remains outstanding. Verify extraction, transcription, rendering, decoding and sandbox behavior on the intended Linux host before relying on it.

## PostgreSQL tests

Database integration uses `REEL_RELAY_TEST_DATABASE_URL`. Tests require an explicitly disposable loopback PostgreSQL database whose name starts with `relay_test`; they delete test rows and reject other destinations. Keep this credential out of source, command history and public logs.

Database concurrency checks do not establish multi-host media-storage correctness. Shared files, locks, configuration and credential keys must also be tested for a distributed deployment.

## Distribution checks

Build from clean source, inspect both wheel and source archive, and smoke-test the installed package away from the checkout:

```sh
.venv/bin/python -m build --no-isolation
.venv/bin/python .github/workflows/verify_distribution.py dist
.venv/bin/pip-audit
```

Use the release allowlist and privacy scanner in addition to packaging checks. Verify that private configuration, runtime data, recorded footage, transcripts, keys, logs, model weights and downloaded assets are absent. Dependency auditing reports known findings for the resolved environment; it is not a security guarantee.

## Browser acceptance

Use an isolated workspace with generic data. Check:

- Fresh bootstrap, first setup, empty queue and missing-dependency instructions.
- Source configuration, capture/editor pause and resume, and transcription installation progress/failure.
- Unsaved form preservation, stale-save rejection and clear error recovery.
- Held-source reassessment, edit/source comparison and exact-export review controls.
- Library filtering, pagination and uploaded-media validation.
- Narrow-screen navigation, clip-preview focus, keyboard operation and no horizontal page overflow.
- Account identity and delivery prerequisites with public posting left disabled during ordinary UI testing.

## External acceptance still required

A real-account Instagram login, token refresh, container processing, media retrieval and publication trial remains outstanding. Complete it deliberately on an intended account with media you may publish. Verify final account identity, playback, captions, attribution, pacing and recovery behavior.

Do not publish detailed private test receipts in release documentation. Report reproducible commands, platform/version, pass/fail/skip results and material limitations using sanitized data.
