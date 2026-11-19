# Operations and recovery

## Start and pause

After bootstrap, run `./relay start --open`. It supervises capture, editing, publishing and the web server. The terminal and machine must remain available. Ctrl+C stops the supervisor's children. No persistent operating-system startup service is installed automatically.

Use **Setup & accounts → Workspace services** to pause or resume capture and editing. Pausing preserves queued candidates and artifacts; it is distinct from passing over a clip. A current operation may need time to finish safely. Public posting has its own control and starts disabled. A pause cannot retract a publication request already sent.

`./relay serve` initially runs only the web interface. Enabling capture or editing in the UI can launch a managed background supervisor behind it. Service controls report whether those workers are managed and fresh. `./relay start` is the simplest way to keep the web server and workers under one foreground supervisor.

For administrators running roles separately:

```sh
./relay run --watch-config
./relay worker --watch
./relay publisher --watch
./relay serve
```

Workers can be restricted with `--stage preparing|judging|rendering|reviewing|revising`. Add concurrency only after measuring memory, CPU, queue age and provider limits. Several processes can share local SQLite; distributed workers require PostgreSQL and coordinated artifact storage.

## Readiness and health

The UI reports sources, editing dependencies, transcription and account/delivery readiness. `/health/live` proves the web process is responding; `/health/ready` checks its database. Neither proves a source is live, a model is usable or posting is authorized.

`./relay doctor` checks local dependencies. `./relay status --json` emits an administrative snapshot. Treat that output as private: raw errors, artifact paths or temporary URLs may be present. Public bug reports should use sanitized reproductions.

Sources report whether intake is accepting candidates, waiting for video or blocked by active editing/pending windows. A stopped enabled source needs a running capture service. Anonymous chat can be refused; EventSub is the supported authenticated alternative.

## Storage and backpressure

The rolling buffer, disk reserve and maximum active processing backlog are configurable. Active editing stages count toward processing capacity; held, rejected, failed and finished clips do not. Each channel separately bounds pending context windows. Recording can continue while new candidate intake waits for editing capacity; subsequent eligible triggers resume automatically.

At a sustained 4 Mbit/s, five minutes of raw video is roughly 150 MB per channel before overhead. Extracted originals, evidence, models and exports consume additional retained storage. Diagnostic segment-session history is bounded, but candidate media is not silently deleted to reach a posting target.

Back up before archiving private runtime data. Remote S3 exports are not automatically deleted; configure an appropriate lifecycle policy on a dedicated export destination. See [Deployment](DEPLOYMENT.md).

## Held and failed edits

A held selection or failed preview is an editorial outcome, not necessarily an infrastructure failure. Inspect the source and listed uncertainty. **Reassess moment** runs selection again with optional context. **Request changes** creates a revision through normal validation and retains previous exports.

For an infrastructure failure, address the recorded cause before using **Retry edit** or `./relay retry ID`. Transient failures already have bounded automatic retries. Expired worker leases can be recovered without discarding the candidate.

A manual preview check must refer to the exact export that was watched and listened to. It cannot clear a changed file or hard technical defect. Comparison playback labeled **THE SOURCE** is not the export being attested.

## Publication recovery

A confirmed manual upload can be recorded with its Reel link. This is an operator attestation, not an API upload or verification. Avoid manually uploading a clip while an automatic send is in progress.

For `publishing` or `publish_unknown`, inspect the account and reconcile the existing container:

```sh
./relay reconcile CANDIDATE_ID
```

Do not reset an uncertain attempt to approved or create a new post as a diagnostic step. A processing-complete container does not prove that an earlier timed-out send failed. Confirmed publication remains recorded even if a later permalink lookup fails.

A definitive `publish_failed` state has a separate recovery path. Follow the error and supported retry action; the ambiguous states intentionally do not use blind retry.

## Backups and upgrades

Back up configuration, database, artifact tree and the matching credential-encryption key. Test restoration in a separate private installation with posting disabled. Encrypted tokens cannot be recovered without their key.

For SQLite, stop services before copying the data directory, or use SQLite's online backup API. Copying only the main database during WAL writes can omit committed data. Use a consistent database backup for PostgreSQL.

Before upgrades, stop workers, keep a backup, install the compatible locked dependencies and run `doctor`. Migrations are versioned; automatic downgrade is not provided. A rollback needs the matching pre-upgrade database, artifacts and key.

Saved settings are applied at safe service/job boundaries. A change to the public web origin requires a server restart because authentication, cookies and allowed origins are established at startup.
