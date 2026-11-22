# Architecture after 1.0

The refactor keeps the 1.0 collection, classification, provider protocols and
export formats. It changes ownership and settlement of concurrent work.
`PROJECT_SPECIFICATION.md` remains the behavior contract; this document explains
where an implementation change belongs.

## Composition and execution

`services/runtime.py:ApplicationServices` composes lazy application resources.
Constructing it imports no Qt, opens no database/device and opens no
browser connection. The CLI and desktop obtain the same lazy collection service there; collection
read/persist operations do not require UI.
Synchronous requests remain in threads;
the store browser adapter remains asynchronous on its creating loop.

`OperationCoordinator` has one main slot and one scan slot. Handles identify
progress and settlement. It does not run tasks, schedule retries or persist a
queue. Cancellation requests stop new work; slots remain occupied while started
work and required dialogs settle. Single-link opening, exports and audio do not
claim the main status bar. Main work takes precedence over scanning there.

`ApplicationServices.worker()` counts actual running thread bodies, rather than
cancellation flags. `io()` awaits the actual thread even when its
caller is cancelled. Shutdown stops admission, signals operations and dialogs,
settles workers, closes their resources and closes SQLite last. The existing
bounded browser close remains. An independent three-second emergency guard
starts during shutdown, before asyncio can wait on an uninterruptible thread.

## UI ownership

`gui/backend.py` orchestrates desktop services and sends view snapshots and
patches through the Qt signal boundary in `gui/bridge.py`. `gui/model.py` owns the
table rows, filtering, sorting and selection by track key. `gui/qml/Main.qml`
owns rendering and input, including the filter controls that drive the model.
Blocking network and disk work runs off the UI thread. The bridge applies
results on the UI thread.

- `player.py` owns the engine; `services/playback.py` owns prepared streams
  independently of row objects.
- Profile dialogs return their answers to `AccountService`; account verification
  and settings writes run there, and worker descriptions exclude token arguments.

## Persistent effects and identity

Workers use copied track inputs, source identity, destination and relevant
settings. Current consent and credentials are read at their request/action
boundary. A view generation rejects late presentation updates. A separate
session generation changes when a playlist is deleted, so a completed old task
cannot write into a new playlist with the same source.

`crate_models.py` keeps crate values and serialization independent of SQLite.
`LibraryService` owns listing, loading, reset/deletion and reconciliation. The
sidebar uses crate headers and request/view generations so an old load cannot
replace a newer choice. Local playlist and folder page loads batch metadata and
analysis reads via `Database.media_many()`. Folder pages reuse indexed metadata
while the file signature matches and apply delayed probe results in batches.

SQLite has one owning thread and explicit short transactions. Repositories read
current records when applying metadata, removal and refresh changes; no network,
browser or dialog occurs inside a transaction. Status and file provenance commit
together, and status mirrors change after commit. The scanner writes batches;
missing-file observations compare a per-key revision before clearing provenance,
so a stale scan cannot erase a completed transfer or later manual decision.
Rendering reads the mirrors. Status changes patch rows by track key, preserving
unrelated download progress.

`schema.py` checks existing schemas read-only and rechecks under
`BEGIN IMMEDIATE`. The current schema is version 5, which adds `copy_results`.
Upgrades take a private, integrity-checked SQLite API backup from a separate
committed reader, including committed WAL data, before applying changes.
Unsupported shapes fail without repair. Reopening the current version needs no
new backup. Backups are retained for manual restoration.

HTTP, browser-extension downloads and local copies share validated temporary-file publication and
a filename lock. Publication and SQLite are separate effects:
`PublishedFileUnrecorded` identifies a completed file whose status write failed.
The file remains, transfer retry is forbidden, and other completed batch files
are still settled. Download batches have no durable queue. Audio exports in copy
mode persist the plan and per-file checkpoints in SQLite, so an interrupted
export can verify existing outputs and resume after restart.

`DownloadWorkflow` owns the single/batch shared attempt, HTTP pool, browser pass
and approved prerequisite retries. It receives copied tracks, a `DownloadRequest`,
a handle, current client/configuration access and narrow event/answer callbacks.
The pool belongs to the workflow and closes only when all submitted work settles.
`DownloadEvent` uses an operation ID and track key; the controller coalesces byte
progress and renders terminal outcomes individually. Downloaded, unrecorded,
failed, cancelled and waiting outcomes remain distinct; unrecorded results carry
the published path. An account/profile wizard
can resume only the pending items it approved. Cancellation also waits for an
in-flight profile save; old dialogs retain their originating cancellation event.

## Integration boundaries

- `soundcloud.py`: authenticated API, discovery and media resolution. Public
  transfers have separate sessions; parallel gates have separate cookie jars.
- `gates/hubs.py`, `providers.py`, `browser.py`: inspection, HTTP protocols,
  Hypeddit browser completion. `gate_models.py` contains shared typed outcomes.
- `stores/bandcamp.py` and `stores/beatport.py`: store-specific selectors,
  cart actions and verification through the browser extension.
  `services/purchases.py` coordinates the batch, manual fallback and Soundiiz handoff.
- `http.py`: URL/redirect rules; `browser.py`: OS handoff; `clipboard.py`:
  clipboard processes; `private_json.py`: private JSON publication.
- `diagnostics.py`: bounded external messages and credential/URL redaction.
  External text is literal and crashes do not render local-variable dumps.

Literal-address SSRF checks retain the known DNS/rebinding limitation. The
refactor is not a claim that DNS pinning, a security audit, Windows ACL protection
or a filesystem/SQLite distributed transaction has been added.

See [verification](refactor/verification.md) for reproducible commands and the
comparison with `171f033`. Deferred implementation work belongs in
[the work log](refactor/progress.md), never in the shipped specification.
