# Refactor work log (not shipped specification)

Scope: the entire reviewed post-1.0 plan, one branch from `171f033`, preserving
1.0 handling/data and provider protocols. No publication or live provider writes.

Completed baseline/extraction/database commits:
- `19fbf59`: locked 787-test baseline.
- `e050c6e`: audio presentation, clipboard and private JSON extraction.
- `84b34f8`: SQLite ownership, protected schema registration and compound state.

Current working checkpoint: 821 offline tests pass on Python 3.14; Ruff and the
spec map pass. The prior 817-test checkpoint passed on 3.12 and 3.13. See
`verification.md` for measurements and the configured CI matrix.

Implemented since the database commit:
- Runtime composition, operation handles and actual-worker resource scopes.
- Collection persistence before delivery, generation-aware metadata/removal
  updates, current-token isolation, current gate consent and file publication.
- Gate hub/provider/browser packages and Bandcamp adapter/purchase service split.
- TUI controllers and separately owned presentation state, pure row queries,
  account/settings services, scanner reconciliation in background batches.
- Shared HTTP/browser/copy finalization, explicit published-but-unrecorded
  results, per-result browser-batch settlement, diagnostic redaction.
- Playback/prefetch A→B→A protection, bounded progress coalescing and callback
  tests, cancellation during publication/dialog/owned asynchronous I/O.
- Architecture documentation and executable import/headless-service boundaries.

Remaining before declaring the user's full plan complete:
- Finish reviewing the placement of download batch orchestration and narrow
  controller/service inputs; remove remaining transitional wiring if redundant.
- Verify keyboard responsiveness under a blocked database and result delivery
  during view changes; fix any remaining scheduling/lifecycle races.
- Complete the final review on both repository-standards and specification axes,
  resolve findings, then rerun required checks on the final tree and record them.
- Record final working commits and matrix results. No release, publish or live
  external mutation is part of this task.

Temporary scripts/logs under `/tmp/digger-*` and `/tmp/*_service*.py` are local
implementation aids; do not blindly rerun one-shot rewriting scripts.
