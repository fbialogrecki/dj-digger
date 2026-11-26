# AGENTS.md

Repository-wide routing and guardrails for `dj-digger`, a Python 3.12+
crate-digging headless CLI and Qt Quick desktop (`dj-digger-gui`).

## Required task startup and skills

- At the start of every new task, before analysis, planning, or edits, read
  `PROJECT_SPECIFICATION.md`: start with its section map and then the relevant
  sections, following this repository's specification-reading rules below.
- Run `git status -sb` and `git worktree list` to confirm the task's branch,
  working directory and existing edits. Keep `main` in the primary checkout
  and use separate worktrees for parallel branches.
- Always load the `graphify` skill and read the existing `graphify-out/graph.json`
  at task startup, including for simple tasks. Inspect its structure and retrieve
  task-relevant nodes and relationships; checking that the file exists is not
  enough. Verify implementation decisions against current source files.
- Automatic `graphify` use is strictly read-only. Never create, rebuild, update,
  repair, cluster, export, or write graph files, sidecars, memories, or reflections
  without an explicit user request for that operation. A missing or stale graph,
  a coding request, or merely invoking the skill does not authorize graph writes.
  Do not install tools or start watchers/hooks as part of automatic graph reading.
  If a query command writes files or needs setup, traverse the JSON in memory.
  These restrictions override the skill's default build/update/write workflow.
- Before coding begins, including later in a conversation, automatically load
  `ponytail` and apply its default `full` level unless the user selects another
  level or opts out. Do not activate it for prose-only tasks.
- These are explicit exceptions to any general manual-only skill policy:
  `graphify` for required read-only startup and `ponytail` before coding.
  Other skills require explicit user invocation. Resolve skills from the current
  skill catalog; on this workstation they live under `~/.agents/skills/`.
- If the specification, graph, or a required skill is missing or unreadable,
  briefly report it and continue with available sources where possible. Do not
  claim it was read or automatically create, install, or update it.
- Reuse context already read in this task while files remain unchanged; do not
  repeat startup on every follow-up message.

## Source Of Truth

- Product, API, data, privacy, and security requirements live in `PROJECT_SPECIFICATION.md`.
- Never read `PROJECT_SPECIFICATION.md` whole unless its "How to read this file" section says
  it is small enough. Start with the first read given there (header and section map), then
  read only the map entries your task touches, or grep when you already know the symbol.
- Verify missing or conflicting descriptions against the implementation and its tests.
  Update the specification when it omits or misstates shipped behavior.

## Specification maintenance

- Update `PROJECT_SPECIFICATION.md` in the same change when altering product behavior, API
  contracts, data models, privacy/security behavior, or ownership boundaries, then refresh the
  section map's line ranges (a stale map sends the next reader to the wrong section) with
  `python3 scripts/spec_section_map.py`, followed by `python3 scripts/spec_section_map.py --check`.
- Do not put plans, target designs, open questions, acceptance checklists, or TODOs in
  `PROJECT_SPECIFICATION.md`.

## Repository map

- `dj_digger/cli.py`, `services/`, `models.py`, `crate_models.py`, `links.py`: entry, collection
  orchestration, shared objects, classification, and exports.
- `dj_digger/soundcloud.py`: SoundCloud API v2 inputs.
- `dj_digger/gui/`, `player.py`, `beats.py`: PySide6/Qt Quick desktop, optional
  in-memory audio preview, and the beat grid the desktop pulses with.
- `dj_digger/db.py`, `schema.py`, `state.py`, `scanner.py`, `config.py`: SQLite
  state, crates, local-file matching, and preferences.
- `dj_digger/auth.py`, `gates/`, `stores/`, `http.py`, `browser.py`: external
  authentication, gate/store integrations, URL handoff to the user's browser, and cart safety.
- `dj_digger/webext/`: the browser extension (`extension/`), its native-host relay, the app-side
  socket `link.py`, and the Playwright-shaped page adapter that gates, login, and cart drive.
- `tests/`: offline suite plus explicitly marked live contract checks.
- `site/`: the project website (static Astro) and the Linux install script it serves.
- `.github/workflows/`: CI, desktop installer builds, scheduled live monitoring, the website
  deploy, and PyPI and Chrome Web Store publishing.

## Engineering rules

- Prefer the standard library and small, direct diffs; do not add dependencies
  for behavior already covered by the platform or existing packages.
- Fix root causes. When a core signature or data structure changes, update every
  caller and the corresponding specification contract.
- Blocking network or disk work in `dj_digger/gui/` must not run on the UI
  thread; use background threads and return UI mutations to the UI thread.
- Offline tests must never reach live endpoints, real XDG application state, or
  the user's music/download folders. Use fakes and `tests/fixtures/`.
- Treat artist links, summary files, redirects, provider responses, and filenames
  as untrusted. Preserve URL scheme/domain-boundary checks, SSRF guards, private
  credential writes, download limits, atomic file replacement, and cart
  revalidation.
- Keep SoundCloud, Spotify, store, and managed-browser credentials out of logs,
  fixtures, documentation, and commits.
- If an intentional shortcut is accepted, document it locally as
  `# ponytail: <reason>`.
- Keep `AGENTS.md` concise. Put repository-wide rules here and detailed
  operational procedures in dedicated documentation; plans and TODOs go in `docs/`.

## Prohibited operations

- Do not publish, deploy, create a release, run production migrations, or invoke
  live/write-capable third-party flows unless the user explicitly requests it.
- Do not delete or rewrite user databases, config, credentials, managed browser
  profiles left by older versions, the user's browser data, downloads, or music files.
- Do not run live pytest markers as part of the default verification command.
- Do not weaken URL, credential, file-download, gate-consent, or cart-mutation
  boundaries to make a test pass.
- Never broaden `allowlist.json`/`host_permissions`, or add remote code, external
  messaging, or generic cookie access to the extension, without an explicit user request.

## Verification

Minimum checks for a normal code change:

```bash
python3 scripts/spec_section_map.py --check
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev pytest
```

Choose focused checks from the repository root while iterating. Qt tests use
PySide6 from the `gui` extra and skip on installations without it.

| Changed area | Local check |
| --- | --- |
| Desktop playlists, status changes and preview controls | `uv run --frozen --extra dev --extra gui --python 3.12 pytest tests/test_gui.py tests/test_gui_table.py tests/test_gui_player.py tests/test_playlist_workflows.py` |
| Audio decoding and seeking | `uv run --frozen --extra dev pytest tests/test_player.py tests/test_hls_audio.py tests/test_local_audio.py` (requires FFmpeg) |
| Browser extension and native host | `uv run --frozen --extra dev pytest tests/test_webext*.py` (Node is required for the JavaScript checks). |
| Website, translations and SEO | From `site/`, run `npm ci && npm run build && python3 scripts/check-seo.py` (CI uses Node 24). |

The default pytest configuration excludes
`live`, `shop_live`, and `hypeddit_live`; run those markers only when explicitly
needed to validate an external contract. Preserve unrelated worktree changes.
