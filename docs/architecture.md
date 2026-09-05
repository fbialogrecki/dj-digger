# Architecture after 1.0

The refactor keeps the 1.0 collection, classification, provider protocols and
export formats. It changes ownership and settlement of concurrent work.
`PROJECT-SPECIFICATION.md` remains the behavior contract; this document explains
where an implementation change belongs.

## Composition and execution

`services/runtime.py:ApplicationServices` composes lazy application resources.
Constructing it imports no Textual, opens no database/device and launches no
Chromium. CLI collection can use the same collection flow without mounting UI.
Textual owns its workers and timers. Synchronous requests remain in threads;
store Playwright remains asynchronous on its creating loop.

`OperationCoordinator` has one main slot and one scan slot. Handles identify
progress and settlement. It does not run tasks, schedule retries or persist a
queue. Cancellation requests stop new work; slots remain occupied while started
work and required dialogs settle. Single-link opening, exports and audio do not
claim the main status bar. Main work takes precedence over scanning there.

`ApplicationServices.worker()` counts actual running thread bodies, rather than
Textual's cancellation flags. `io()` awaits the actual thread even when its
caller is cancelled. Shutdown stops admission, signals operations and dialogs,
settles workers, closes their resources and closes SQLite last. The existing
bounded browser close and process-exit fallback remain.

## UI ownership

`DiggerApp` composes widgets, routes actions/events and handles lifecycle.
Controllers receive concrete services, the presentation state they use and
explicit callbacks; none receives the app or a universal app facade.

- `presentation.PlaylistState`: rows, filters, sort, selection, anchor and view
  generation. `filters.py`, `playlist.py` and `render.py` manage querying and
  painting; `TrackTable` retains Textual's cursor and viewport.
- `SidebarState` and `crates.py`: playlist listing, switching and local removal.
- `AudioState`, `playback.py` and `audio.py`: transport presentation and request
  generations. `player.py` owns the engine; `services/playback.py` owns prepared
  streams independently of row objects.
- Download, cart and scan controllers keep their own presentation bookkeeping.
  Network/disk effects belong to services; result delivery only updates UI.
- Profile dialogs return `GateProfileAnswer`. Account verification and settings
  writes run in `AccountService`; worker descriptions exclude token arguments.

The pinned Textual 8.x integration uses private hooks for incremental table size
updates and safe fatal-error presentation. Existing viewport, keymap and crash
regressions cover these dependencies.

## Persistent effects and identity

Workers use copied track inputs, source identity, destination and relevant
settings. Current consent and credentials are read at their request/action
boundary. A view generation rejects late presentation updates. A separate
session generation changes when a playlist is deleted, so a completed old task
cannot write into a new playlist with the same source.

SQLite has one owning thread and explicit short transactions. Repositories read
current records when applying metadata, removal and refresh changes; no network,
browser or dialog occurs inside a transaction. Status and file provenance commit
together, and status mirrors change after commit. The scanner writes batches.
Rendering reads the mirrors rather than issuing a query per row.

`schema.py` recognizes existing schema read-only. It registers the unchanged 1.0
schema as version 1 only after a private, integrity-checked SQLite API backup
(including WAL), then rechecks under `BEGIN IMMEDIATE`. Unknown/older/newer shapes
fail without repair. Reopening version 1 creates no new backup. Backups are
retained; restoration is a user decision.

HTTP, Chromium and local copies share validated temporary-file publication and
a filename lock. Publication and SQLite are separate effects:
`PublishedFileUnrecorded` identifies a completed file whose status write failed.
The file remains, transfer retry is forbidden, and other completed batch files
are still settled. There is no durable job journal or process-crash resumption.

## Integration boundaries

- `soundcloud.py`: authenticated API, discovery and media resolution. Public
  transfers have separate sessions; parallel gates have separate cookie jars.
- `gates/hubs.py`, `providers.py`, `browser.py`: inspection, HTTP protocols,
  Hypeddit browser completion. `gate_models.py` contains shared typed outcomes.
- `stores/bandcamp.py`: recorded selectors and adapter behavior.
  `services/purchases.py`: batch flow and Soundiiz handoff. Pure store models,
  URL rules, matching and parsing retain their existing modules.
- `http.py`: URL/redirect rules; `browser.py`: OS handoff; `clipboard.py`:
  clipboard processes; `private_json.py`: private JSON publication.
- `diagnostics.py`: bounded external messages and credential/URL redaction.
  External TUI text is literal and crashes do not render local-variable dumps.

Literal-address SSRF checks retain the known DNS/rebinding limitation. The
refactor is not a claim that DNS pinning, a security audit, Windows ACL protection
or a filesystem/SQLite distributed transaction has been added.

See [verification](refactor/verification.md) for reproducible commands and the
comparison with `171f033`. Deferred implementation work belongs in
[the work log](refactor/progress.md), never in the shipped specification.
