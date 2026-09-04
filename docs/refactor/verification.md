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
