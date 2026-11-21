# Navigating the Graphify graph

[Explore the public graph](https://djdigger.app/graph/) or [download its JSON](https://djdigger.app/graph/graph.json). The website serves a snapshot of [source commit `4bed67a`](https://github.com/fbialogrecki/dj-digger/commit/4bed67ac6cb465a743254edbf5e692883db8066c). Use it to find related modules and follow references, then check the source and tests for behavior.

Local Graphify runs write separate artifacts to the Git-ignored `graphify-out/` directory. Those artifacts can include uncommitted work. Check the recorded build commit before using a local graph to navigate another checkout.

For how to use the app, see the [README](../README.md), the [installation guide](installation.md) and the [workflow guide](workflows.md). The entries below describe earlier local builds.

# Desktop usability correction — 2026-09-09

`graphify update .` re-extracted the eight changed GUI/test files: 3,723 nodes,
9,593 edges, 155 communities. The GUI backend now owns a validating `form()`
loop and typed dialog fields; the bridge exposes message levels, pinned folders
and explicit whole-view actions; the table model exposes counts, store totals
and sort state. The graph records structure only; QML behavior is verified by
the rendering/input tests and offscreen screenshots, not inferred from edges.

# Desktop waveform and explorer correction — 2026-09-09

The local AST refresh now contains 3,712 nodes, 9,569 edges and 137 communities.
GUI and TUI use `waveform.py` for column levels. The GUI bridge owns the native
Qt filesystem model; local waveform work publishes through the existing signal
boundary after a loaded-object identity check. QML behavior is verified through
rendering/input tests, not inferred from graph edges. Older counts below are
historical snapshots.

# Desktop graph refresh — 2026-09-09

`graphify update .` refreshed local AST navigation for the desktop branch:
3,701 nodes, 9,530 edges, 162 communities. The graph includes the new GUI
backend/bridge/model and shared `rows.py`/`playlist.py` ownership. It is not proof
of runtime behavior, and changed Markdown has not received semantic extraction.
Qt Linguist `.ts` catalogs are XML rather than TypeScript and are now excluded
from future scans. The historical release snapshot below retains its own counts.

# Knowledge-graph notes

Updated on 2026-09-09 from the `feat/local-library-club-export` working tree,
for the 1.1.0 release, including the SoundCloud playback access-policy fixes.

## Corpus and outputs

The filtered corpus contains **140 files**. Code was refreshed after the playback fixes; the final release refresh updates
the structure of two documents.
The undirected navigation graph contains **3,605 nodes, 8,513 edges and 182
communities**.

`.graphifyignore` excludes agent tooling, old design plans, third-party fixture
payloads and this generated commentary. Query-memory files added independently
by the detector were removed from this scan so prior answers do not become
architecture evidence.

In that local build, `graph.html` and `graph.json` supported navigation.
`GRAPH_REPORT.md` recorded cohesion scores, and `GRAPH_HEALTH.md`,
`extraction.json` and `diagnostics.json` recorded extraction details.

Changed-document semantic nodes were replaced by current structural extraction
and six narrowly anchored contract links labelled INFERRED. This is less rich
than a full semantic re-extraction. Two old cross-document hyperedges had no
surviving members and were omitted. Unchanged document nodes retain their earlier
extraction; historical release/implementation documents are not current contracts.
The manifest records the current code hashes and filtered corpus. Changed Markdown
remains eligible for semantic extraction: structural parsing is not a semantic
cache hit.

## Current navigation paths

- Playback resolution distinguishes provider BLOCK policy, missing streams and
  unsupported formats. BLOCK takes precedence over streamable/transcoding fields.
- Playback resolution prefers progressive MP3, with bounded MP3 HLS buffering
  in `hls_audio.py` when progressive is absent. Waveform colors follow position
  only; the explorer keeps native scrollbar interaction with a thin renderer.

- Repost collection uses the stream endpoint and rejects repeated pagination pages.
- Table layout is separate from configured sorting and row identity. Local views
  retain stored BPM/Key without automatic analysis or changing online preferences.
- F4 reads current view counts without scanning; settings retain mounted fields
  while switching tabs or opening account controls.
- Export button identity determines whether a plan is approved or cancelled.
  File export still uses device rules, PCM/container verification, playback leases
  and journaled replacement recovery.
- Local metadata resolution exposes a separate source for each field without
  extending serialized Track values. Analysis stays in a bounded subprocess;
  private logs/JSONL remain separate from the removed analysis results panel.
- The standalone benchmark measures raw estimates and verified-reference coverage.
- Profile import retains provider IDs, pagination/session checks and stable
  playlist persistence. Private import remains distinct from public profile digging.

## Integrity and measurement limits

The exported graph has unique node IDs and valid edge endpoints. Raw extraction
still has **676 dangling import/dependency edges** and **346 same-endpoint relations
collapsed in the undirected representation**, with no missing endpoint fields or
self-loops. Raw extraction preserves the evidence; the navigation graph is not a
complete directed call graph or proof of runtime behavior.

This refresh used local AST/Markdown extraction and no remote LLM calls
(**0 external LLM tokens**). Previous semantic extraction usage remains unknown
in the historical cost ledger. Community labels reuse surviving-member labels
with local fallbacks; they are navigational hints, not reviewed architecture.

Application validation: **899 offline tests passed, 82 deselected**. Seven focused
UI tests passed after the last settings layout/copy adjustment. Ruff, the generated
specification map and `git diff --check` passed. These checks do not establish
physical CDJ support or fresh macOS/Windows execution results.
