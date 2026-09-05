# Refactor work log

Scope: the reviewed post-1.0 architecture plan, one branch from `171f033`,
preserving 1.0 handling/data except the specified concurrency/safety corrections.
Implementation and local verification are complete. No publication, push, user
data migration or live provider write was executed.

Working commits:

- `19fbf59`: locked 787-test baseline.
- `e050c6e`: audio presentation, clipboard and private JSON extraction.
- `84b34f8`: SQLite ownership, protected schema registration and compound state.
- `248ba24`: runtime services, gates/stores, operation handles and composed TUI.
- `977974c`: reviewed scanner, generation, credential, playback and shutdown fixes.
- `d9a0c3f`: shared download workflow and CLI/TUI collection composition.
- `f485591`: library ownership/pure models, settled profile/browser cancellation,
  distinct file outcomes and ordered/view-safe status actions.

Final local results: 838 offline tests on each of Python 3.12, 3.13 and 3.14;
four recorded Chromium tests; Ruff, spec map and diff whitespace checks pass.
Both review axes have no outstanding confirmed findings. See
[verification](verification.md), [review](review.md) and
[architecture](../architecture.md) for evidence and ownership.

The configured nine-cell CI matrix is retained; the macOS/Windows cells were
not executed in this Linux workspace and require remote CI. This is a validation
limit, not a claim that those cells passed. No automatic CI/publication flow was
triggered.

Temporary `/tmp/digger-*` environments, browser binaries, benchmark snapshots,
logs and one-shot rewriting scripts were implementation aids. They are not
application state and should not be blindly replayed.
