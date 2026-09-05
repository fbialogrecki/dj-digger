# Post-1.0 architecture verification

Base: `v1.0.0`, `171f03365dda03d4a5e5e9e5efff57d041b5c1c7`.
Verified implementation: `f485591`, 2026-09-05.
Environment: `uv run --frozen --extra dev`, using the committed `uv.lock`.

Baseline: **787 passed, 81 deselected, 67.51 seconds**.
The default suite excludes live, shop_live, hypeddit_live, bandcamp_dom and
shop_mutate. No provider mutation or publication was executed.

## Final checks

| Local Linux environment | Offline result | Duration |
| --- | --- | ---: |
| Python 3.12.13 | 838 passed, 81 deselected | 80.79 s |
| Python 3.13.13 | 838 passed, 81 deselected | 76.58 s |
| Python 3.14.7 | 838 passed, 81 deselected | 80.40 s |

Ruff, specification section map and `git diff --check` passed. Separate temporary
environments retained the same lockfile for Python 3.12/3.13; the project
interpreter remained 3.14.

Four additional recorded Bandcamp tests passed in Chromium (**4 passed, 2.90 s**).
The browser was installed under `/tmp/digger-playwright-browsers`; the tests use
fresh contexts whose every request is fulfilled from fixtures or aborted. No
managed user profile or live cart flow was used.

```sh
python3 scripts/spec_section_map.py --check
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev pytest
UV_PROJECT_ENVIRONMENT=/tmp/digger-refactor-py312 uv run --frozen --extra dev --python 3.12 pytest
UV_PROJECT_ENVIRONMENT=/tmp/digger-refactor-py313 uv run --frozen --extra dev --python 3.13 pytest
PLAYWRIGHT_BROWSERS_PATH=/tmp/digger-playwright-browsers uv run --frozen --extra dev pytest -m bandcamp_dom tests/test_bandcamp_dom.py
```

The configured CI matrix remains Ubuntu, macOS and Windows × Python 3.12, 3.13
and 3.14, using frozen lint/test commands. **Only Linux was executed locally.**
The six macOS/Windows cells require remote CI; no workflow, push or release was
triggered. The local results do not claim verification on those operating systems.

## Acceptance evidence

| Contract | Executable evidence |
| --- | --- |
| CLI, JSON, nine-column CSV, saved HTML and config | `test_cli.py`, `test_links.py`, `test_dig.py`, `test_html_fallback.py`, `test_config.py` |
| Services without Textual/devices/Chromium; pure models; provider/UI boundaries | `test_architecture.py` |
| Admission, cancellation and actual worker/resource settlement | `test_operations.py`, `test_download_workflow.py`, `test_tui_lifecycle.py` |
| Cancellation during HTTP, profile/auth dialog, browser and publication | `test_tui.py`, `test_download_workflow.py`, `test_gates.py`, `test_file_results.py` |
| Stale views, deletion/recreation, metadata/NEW/removal preservation | `test_file_results.py`, `test_library.py`, `test_tui.py` |
| Status/provenance rollback, stale scanner invalidation, published-but-unrecorded result | `test_state.py`, `test_file_results.py`, `test_download_workflow.py` |
| Single SQLite owner, unknown schema refusal, backup failure, WAL-aware one-time backup | `test_db.py` |
| Session/token isolation, live credentials, revoked consent and redirect guards | `test_soundcloud.py`, `test_auth.py`, `test_gates.py`, `test_browser.py` |
| Filename collisions, size/HTML checks, cancellation and private writes | `test_file_results.py`, `test_soundcloud.py`, `test_gates.py`, `test_auth.py` |
| Redaction and literal UI text | `test_diagnostics.py`, `test_tui.py` |
| Keyboard order/responsiveness, cursor/viewport stability, playback/prefetch A→B→A | `test_tui.py`, `test_player.py`, benchmark below |
| Existing store algorithms/selectors and recorded browser pages | `test_cart.py`, `test_bandcamp_dom.py` |

Existing scenarios were updated with their moved modules, rather than replaced
by tests only of new mocks. Added regressions exercise real SQLite transactions,
thread pools, blocked writes, Textual key dispatch, adapter cancellation results
and isolated recorded browser pages.

## Playlist comparison

Run `uv run --frozen --extra dev python scripts/benchmark_playlist.py --repo PATH`
against an extracted `git archive 171f033` and this checkout. The script blocks
requests, redirects XDG paths to a temporary directory, scans an empty directory,
and uses identical generated tracks, terminal dimensions and filter sequences.
No user music folders, profiles or database are used.

| Measurement | 1.0 / 171f033 | Final / f485591 |
| --- | ---: | ---: |
| Tracks | 1000 | 1000 |
| Filter updates | 15 | 15 |
| Median synchronous table-update callback | 31.652 ms | 32.349 ms |
| Table clears | 15 | 15 |
| Database calls during those updates | 0 | 0 |

The measured callback median changed by about +2.2%, with no extra table rebuilds
or database reads. These local timings do not measure provider latency or every
terminal. Viewport and row-update regressions additionally cover incremental
status/download painting, and blocked-write tests verify keyboard responsiveness.
Byte progress is coalesced to one latest value per track/operation; terminal
outcomes remain separate.

See the [final review](review.md) for closed findings. Known DNS/rebinding,
Windows ACL and cross-filesystem/SQLite atomicity limitations remain documented;
this refactor adds no durable job journal or process-crash resumption.
