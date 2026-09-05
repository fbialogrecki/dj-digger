# Post-1.0 architecture verification

Base: `v1.0.0`, `171f03365dda03d4a5e5e9e5efff57d041b5c1c7`.
Environment: `uv run --frozen --extra dev`, using the committed `uv.lock`.

Baseline on 2026-09-05: **787 passed, 81 deselected, 67.51 seconds**.
The default suite excludes live, shop_live, hypeddit_live, bandcamp_dom and
shop_mutate. No provider mutation or publication is authorized by this refactor.

Existing scenarios are retained when moving module ownership. Behavior corrections
are committed separately from mechanical extraction. Acceptance follows the
reviewed user plan: services without Textual, composition of the TUI, operation
identity and settlement, generation-safe results, explicit resource ownership,
protected schema registration, and preservation of the 1.0 external contracts.

## Current checkpoint

Linux / Python 3.14.7: **821 passed, 81 deselected, 76.37 seconds**.
The preceding 817-test checkpoint also passed on Python 3.12.13 (81.49 s)
and 3.13.13 (75.24 s), in separate environments using the same lockfile.
Final review and the final matrix are recorded after the remaining integration.

The CI matrix remains Ubuntu, macOS and Windows × Python 3.12, 3.13 and 3.14.
Its lint/test commands now use `--frozen`. Only Linux was executed locally;
the other operating systems require CI. No workflow or release was triggered.

## Playlist comparison

Run `uv run --frozen --extra dev python scripts/benchmark_playlist.py --repo PATH`
against an extracted `git archive 171f033` and this checkout. The script blocks
requests, redirects XDG paths to a temporary directory, scans an empty directory,
and uses identical generated tracks, terminal dimensions and filter sequences.
No user music folders, profiles or database are used.

| Measurement | 1.0 / 171f033 | Refactor checkpoint |
| --- | ---: | ---: |
| Tracks | 1000 | 1000 |
| Filter updates | 15 | 15 |
| Median synchronous table-update callback | 31.652 ms | 32.020 ms |
| Table clears | 15 | 15 |
| Database calls during those updates | 0 | 0 |

These are local callback timings, not a claim about provider latency or every
terminal. The existing viewport/row-update tests additionally cover incremental
status/download painting without rebuilding or moving the viewport.
