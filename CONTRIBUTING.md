# Contributing

Contributions should make a fresh self-hosted installation easier to run, inspect and recover. A working browser flow, clear failure state and documented limitation matter as much as the underlying pipeline.

## Development setup

Install Python 3.12 and the system media prerequisites listed in [README](README.md), then run `./bootstrap.sh`. Frontend work also needs Node.js 22.12 or later and `npm --prefix frontend ci`. Use a separate private workspace with synthetic fixtures; never run tests against an active capture or publishing queue.

The builtin engine must remain independent of any parent editing repository. Local models are supported without a mandatory paid API. New providers should declare what evidence leaves the machine and preserve the same structured decision, review and privacy boundaries.

## Changes and checks

Keep changes focused and add behavior-level tests for meaningful state transitions or failure cases. Cover rejected inputs and concurrency where the change affects config saves, queue ownership, review validity or publication. Avoid tests that merely restate implementation details.

```sh
.venv/bin/ruff check reelrelay tests
.venv/bin/ruff format --check reelrelay tests
.venv/bin/python -B -m unittest discover -s tests -v
npm --prefix frontend run check
npm --prefix frontend run format:check
```

The frontend build updates `reelrelay/web/`; commit the generated output with corresponding UI changes. [Testing](docs/TESTING.md) covers opt-in media/database checks and distribution checks. Report which checks ran and which were skipped. Do not describe a simulated platform test as a real publication or a command-construction test as Linux media verification.

When changing HTTP requests or responses, update backend validation, frontend runtime schemas, tests and [API documentation](docs/API.md) together. Read [Architecture](docs/ARCHITECTURE.md) before changing state ownership or retry behavior.

## Pull requests and issues

Explain the concrete problem, resulting behavior and verification. Include reproduction steps with synthetic data where possible. For UI changes, include desktop and narrow-screen screenshots made with generic data. Mention any migration, deployment prerequisite or known limitation.

Do not attach real credentials, platform authorization codes, signed URLs, personal paths, unredacted logs, private transcripts or recorded footage. Public issue examples should be minimal and sanitized. Report suspected vulnerabilities through the process in [SECURITY.md](SECURITY.md).

Contributions to the application are provided under its MIT license. Do not add media, model weights or copied source whose redistribution terms are incompatible or unclear. Keep third-party attribution and license records with any intentionally included material.
