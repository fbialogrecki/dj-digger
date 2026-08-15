# Knowledge-graph notes

Answers to the questions `/graphify` raised for this repo, kept here because
`graphify-out/` is regenerated on every run and these findings are not.

> **Stale as of 0.6.0.** The graph these notes describe was built before the
> ponytail cycle. Since then `dj_digger/ui/` has been deleted, `tui.py` has
> become a package of thirteen modules, and `browser.py`, `scanner.py` and
> `db.py` have grown real roles. The reasoning below still holds - especially
> the caveat about heuristic `uses` edges - but the node counts and centrality
> figures describe a tree that no longer exists. Re-run `/graphify` to refresh
> it.

Graph built at commit `c2e8c1e` (v0.5.1): 1289 nodes, 3142 edges, 85 communities,
from 43 code files and 11 documents. Betweenness figures below were recomputed
directly from `graphify-out/graph.json` with NetworkX, so they differ in the
third decimal from the ones printed in `GRAPH_REPORT.md`.

## First, a caveat that changes how you read the whole graph

242 of the 3142 edges (7.7%) are `uses` edges with `confidence_score = 0.5`,
emitted by the AST pass. They are **module co-location heuristics, not real
usage**: every class defined in a module that imports `Track` gets a
`Track --uses--> ThatClass` edge. That is how the graph ends up claiming
`Track uses AskLinkScreen`, `Track uses FakeDevice` and
`SoundCloudClient uses SettingsScreen`, none of which exist in the code.

Rule of thumb when reading this graph: **filter out `relation == "uses" and
confidence_score == 0.5` before drawing any conclusion.** Everything below
reports the number before and after that filter.

## Why does `Track` bridge so many communities?

Because it was designed to. `dj_digger/models.py` says so in its own docstring:

> Lives in its own module so `soundcloud`, `html_fallback`, `links` and `tui`
> can all speak the same vocabulary without importing each other.

The graph confirms the design held. `Track` has **19 `imports` edges** — nine
production modules (`html_fallback`, `library`, `links`, `player`, `scanner`,
`soundcloud`, `tui`, `ui/app`, `ui/table`) and ten test modules — plus 25
`references` and 42 `calls`.

| metric | all edges | heuristics filtered |
| --- | --- | --- |
| degree | 135 | 98 |
| communities touched | 53 | 37 |
| betweenness | 0.336 | 0.266 |

Filtering the heuristics costs `Track` only 21% of its betweenness, so the
bridging is **real, and it is the intended architecture** — a shared dataclass
that lets the fetch layer, the categoriser, the player and the UI avoid
importing each other. This is not a finding to act on.

Two of the nine production importers are `dj_digger/ui/app.py` and
`dj_digger/ui/table.py`, which nothing imports in turn — see the last section.

## Why does `DiggerApp` bridge 31 communities?

It mostly does not. `DiggerApp` has 137 edges, but **112 of them are `method`
edges** — its own methods — and 117 of its 125 real edges originate in
`dj_digger/tui.py` alone. Its betweenness barely moves when the heuristics are
filtered (0.184 → 0.179) because its mass is internal, not connective.

So the honest reading is not "important bridge" but **God Class**: one class
carrying 112 methods across 1400 lines of `tui.py`, holding playback, digging,
downloads, filtering, crate management, rendering and settings at once. That is
a real structural finding, and it is a bigger one than anything in the ponytail
audit — but splitting it is a refactor, not a deletion, so it sits outside the
audit's "what to cut" scope.

## Why does `Player` bridge 23 communities?

Same shape, smaller: 62 edges of which 27 are its own methods, 30 of 46 real
edges from `player.py` itself. Its betweenness actually *rises* when heuristics
are filtered (0.074 → 0.085), meaning the noise edges were routing paths
*around* it. `Player` is a cohesive module boundary, not a tangle. Nothing to do.

## Are the 40 INFERRED edges on `Track` correct?

**No — 38 of 40 are wrong.** Breakdown:

| edge | score | verdict |
| --- | --- | --- |
| `Track --shares_data_with--> categorise_link()` (AGENTS.md) | 0.95 | correct |
| `Track --uses--> SoundCloudClient` (soundcloud.py) | 0.95 | correct as an undirected association; the real direction is the reverse |
| `Track --conceptually_related_to--> Rejected BPM Column` (spec) | 0.85 | correct and genuinely useful — the spec records *why* there is no `bpm` field |
| 37 × `Track --uses--> <class>` | 0.50 | **artifacts**, the module co-location heuristic above |

## Are the 28 INFERRED edges on `SoundCloudClient` correct?

**No — 26 of 28 are wrong**, same cause. The two that hold up are worth keeping:

- `SoundCloudClient --implements--> API v2 Batch Hydration Digging` (0.95, README) — correct.
- `SoundCloudClient --semantically_similar_to--> extract_from_hydration()` (0.85) — correct and non-obvious: the api-v2 client and the saved-HTML parser are two independent solutions to the same "get every track id out of a playlist" problem, which is exactly why `dig.py` can swap between them.

The remaining 26 are `uses` @ 0.50 pointing at every class in `tui.py`,
`player.py` and `tests/test_soundcloud.py`.

## What are the weakly-connected nodes?

`GRAPH_REPORT.md` names 8; the fuller picture is **351 nodes at degree ≤ 1**, and
exactly one at degree 0.

- **`dj-soundcloud-digger` (degree 0, from `pyproject.toml`)** — the project node
  itself. Nothing links to it because no extractor emits "package contains
  module" edges. Expected, harmless.
- **`Code-First Output Pattern`, `net: -N lines possible Metric`,
  `Repo-Wide Bloat Hunt`, `no-trigger Rot Risk Tag`,
  `Benchmark Median Scoreboard`** — all from `.agents/skills/ponytail-*/SKILL.md`.
  These are copies of the ponytail tooling, describing *process*, not this
  application. Their isolation is correct: they should not connect to
  `dj_digger/`. If you want a cleaner graph, exclude `.agents/` from the scan.
- **`PyPI Trusted Publisher (OIDC id-token)`** — from `.github/workflows/publish.yml`,
  CI infrastructure with no code counterpart. Also correct isolation.
- The remaining ~340 are one-line `rationale` nodes (docstrings) and bare type
  names (`Path`, `App`, `Click`, `Connection`) with a single anchoring edge.
  Normal extractor output, not documentation gaps.

**Nothing here is a genuine gap.** The "weakly-connected nodes" signal is firing
on tooling and stdlib type names, not on under-documented application code.

## Where the graph and the ponytail audit agree

`dj_digger/ui/app.py` scores **betweenness 0.148 — fifth highest in the whole
graph** — for a file that nothing imports. In an undirected graph a module that
imports from ten places looks like a hub even when nobody imports it back. The
graph is measuring an orphan as a load-bearing connector, which is precisely the
audit's largest finding: the entire `dj_digger/ui/` package (633 lines) is a
second, never-executed TUI. It entered in one commit (`9610fba`, v0.5.0) and has
not been touched since, and `ui/app.py:184` calls
`SoundCloudClient.resolve_crate(target, max_tracks=…)` — a method and an option
that have never existed.
